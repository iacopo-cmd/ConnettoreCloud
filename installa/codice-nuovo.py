#!/usr/bin/env python3
"""Crea un codice del connettore: python3 installa/codice-nuovo.py <nome> [profilo=root]
In key/codici.json va solo l'impronta sha256; il codice in chiaro finisce in key/codici-in-chiaro.txt
(root 600) e NON viene stampato a video. Poi: docker restart connettore-cloud."""
import hashlib, json, os, secrets, sys
from datetime import datetime, timezone
base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "key")
UID = int(os.environ.get("CONNETTORE_UID", "10110"))
nome = sys.argv[1]; profilo = sys.argv[2] if len(sys.argv) > 2 else "root"
codice = secrets.token_urlsafe(32)
p = os.path.join(base, "codici.json")
d = json.load(open(p)) if os.path.exists(p) else {}
d[hashlib.sha256(codice.encode()).hexdigest()] = {"nome": nome, "profilo": profilo,
    "creato": datetime.now(timezone.utc).isoformat(timespec="seconds")}
json.dump(d, open(p, "w"), indent=1)
os.chown(p, 0, UID); os.chmod(p, 0o640)
c = os.path.join(base, "codici-in-chiaro.txt")
with open(c, "a") as f:
    f.write(f"{nome}\t{profilo}\t{codice}\n")
os.chmod(c, 0o600)
print(f"codice per «{nome}» ({profilo}) creato: lo trovi in key/codici-in-chiaro.txt")
