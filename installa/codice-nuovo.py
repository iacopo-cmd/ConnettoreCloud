#!/usr/bin/env python3
"""Crea un codice del connettore.

  python3 installa/codice-nuovo.py <nome>                       profilo root: vede tutto
  python3 installa/codice-nuovo.py <nome> ristretto --container 'app1-*' [--container ...]
                                   [--servizi 'nginx.service'] [--senza-macchina]
      profilo ristretto: vede solo quei container (e quei servizi systemd), i loro log, e la salute generale
      della macchina (CPU, RAM, dischi) se non c'è --senza-macchina. Utenti, SSH, Claude, memorie, processi e
      porte no. Strumenti: riassunto, container, log, risorse.

In key/codici.json va solo l'impronta sha256; il codice in chiaro finisce in key/codici-in-chiaro.txt
(root 600) e NON viene stampato a video. Poi: docker restart connettore-cloud."""
import argparse, hashlib, json, os, secrets
from datetime import datetime, timezone

a = argparse.ArgumentParser()
a.add_argument("nome")
a.add_argument("profilo", nargs="?", default="root", choices=["root", "ristretto"])
a.add_argument("--container", action="append", default=[], help="nome o schema (app1-*), anche più volte")
a.add_argument("--servizi", action="append", default=[], help="unità systemd (nginx.service), anche più volte")
a.add_argument("--senza-macchina", action="store_true", help="non vede neanche CPU, RAM e dischi")
x = a.parse_args()
if x.profilo == "ristretto" and not (x.container or x.servizi):
    a.error("il profilo ristretto vuole almeno un --container o un --servizi")

base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "key")
UID = int(os.environ.get("CONNETTORE_UID", "10110"))
codice = secrets.token_urlsafe(32)
voce = {"nome": x.nome, "profilo": x.profilo, "creato": datetime.now(timezone.utc).isoformat(timespec="seconds")}
if x.profilo == "ristretto":
    voce.update({"container": x.container, "servizi": x.servizi, "macchina": not x.senza_macchina})

p = os.path.join(base, "codici.json")
d = json.load(open(p)) if os.path.exists(p) else {}
d[hashlib.sha256(codice.encode()).hexdigest()] = voce
json.dump(d, open(p, "w"), indent=1)
os.chown(p, 0, UID); os.chmod(p, 0o640)
c = os.path.join(base, "codici-in-chiaro.txt")
with open(c, "a") as f:
    f.write(f"{x.nome}\t{x.profilo}\t{codice}\n")
os.chmod(c, 0o600)
cosa = "" if x.profilo == "root" else f" — container {x.container or '-'}, servizi {x.servizi or '-'}, macchina {'sì' if voce['macchina'] else 'no'}"
print(f"codice per «{x.nome}» ({x.profilo}{cosa}) creato: lo trovi in key/codici-in-chiaro.txt")
