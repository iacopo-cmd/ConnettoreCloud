"""Connettore cloud – server MCP in SOLA LETTURA: Claude chiede «cosa sta facendo questa macchina?».

Indirizzi: <base>/mcp con OAuth (claude.ai: si incolla il proprio codice nella pagina /accedi) oppure
<base>/c/<codice>/mcp (Claude Code e client senza OAuth). Nel file dei codici c'è solo l'impronta sha256.
Versione 1: profilo «root» = vede tutto. Nessuno strumento cambia, avvia, ferma o cancella niente.
Fonti: dati/stato-host.json (raccoglitore root, di base ogni 5 minuti: anche i container Docker),
journald montato in sola lettura, /proc dell'host (pid: host), LLM locale via http.
"""
import fnmatch, json, os, re, subprocess, time, urllib.parse, urllib.request
from datetime import datetime, timezone

import psutil
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.filtri import pulisci, taglia
from app.oauth import AMBITO, Fornitore, impronta, pagina

NOME_MACCHINA = os.environ.get("NOME_MACCHINA") or os.uname().nodename
STATO = os.environ.get("STATO_HOST", "/dati/stato-host.json")
CODICI = os.environ.get("CODICI", "/key/codici.json")
REGISTRO = os.environ.get("REGISTRO", "/dati/registro.jsonl")
LLM_URLS = [u for u in os.environ.get("LLM_URLS", "http://127.0.0.1:10400").split(",") if u]
HOST = os.environ.get("ASCOLTA_HOST", "127.0.0.1")
PORTA = int(os.environ.get("ASCOLTA_PORTA", "63200"))
NOMI_HOST = [h for h in os.environ.get("NOMI_HOST", "127.0.0.1,localhost").split(",") if h]
MAX_RIGHE = 200
OGNI_MINUTI = int(os.environ.get("RACCOLTA_OGNI_MINUTI") or 5)


def _indirizzo_pubblico() -> str:
    """INDIRIZZO_PUBBLICO nel .env, altrimenti https://<primo nome non locale>:<porta> se c'è il certificato."""
    if os.environ.get("INDIRIZZO_PUBBLICO"):
        return os.environ["INDIRIZZO_PUBBLICO"].rstrip("/")
    nomi = [h for h in NOMI_HOST if not h.startswith(("127.", "localhost", "::1"))]
    if os.environ.get("TLS_CERT") and nomi:
        return f"https://{nomi[0]}" + ("" if PORTA == 443 else f":{PORTA}")
    return f"http://127.0.0.1:{PORTA}"


BASE = _indirizzo_pubblico()
FORNITORE = Fornitore(os.environ.get("OAUTH_DATI", "/dati/oauth.json"), CODICI, BASE)

ISTRUZIONI = f"""Connettore cloud della macchina «{NOME_MACCHINA}», solo lettura.
Per una domanda generica («come sta», «cosa sta facendo», «chi c'è dentro») usa prima `riassunto`.
Orari in UTC. Segreti, mail e telefoni nell'output sono nascosti apposta («nascosto»)."""

mcp = FastMCP("Connettore cloud", instructions=ISTRUZIONI, host=HOST, port=PORTA,
              stateless_http=True, json_response=True,
              auth_server_provider=FORNITORE,
              auth=AuthSettings(issuer_url=BASE, resource_server_url=f"{BASE}/mcp",
                                client_registration_options=ClientRegistrationOptions(
                                    enabled=True, valid_scopes=[AMBITO], default_scopes=[AMBITO]),
                                revocation_options=RevocationOptions(enabled=True),
                                required_scopes=[AMBITO], validate_token_resource=True),
              transport_security=TransportSecuritySettings(
                  enable_dns_rebinding_protection=True,
                  allowed_hosts=[f"{h}:{PORTA}" for h in NOMI_HOST] + [f"{h}:*" for h in NOMI_HOST] + NOMI_HOST,
                  allowed_origins=["https://claude.ai", "https://www.claude.ai"]))


# ---------------------------------------------------------------- chi chiama, registro, uscita

def _chi(ctx: Context | None) -> dict:
    """Chi chiama: dal token (OAuth o /c/<codice>) all'impronta del codice, e da lì alla voce in key/codici.json."""
    try:
        req = ctx.request_context.request
    except Exception:
        return {"nome": "locale", "profilo": "root", "ip": "", "permessi": {}}   # stdio / prove dirette
    ip = req.headers.get("x-connettore-ip", "")
    utente = req.scope.get("user")
    tok = getattr(utente, "access_token", None)
    voce = FORNITORE.voce_codice(tok.subject) if tok and tok.subject else None
    if not voce:
        return {"nome": "?", "profilo": "?", "ip": ip, "permessi": {}}
    permessi = {k: voce.get(k) for k in ("container", "servizi", "macchina") if k in voce}
    return {"nome": voce["nome"], "profilo": voce["profilo"], "ip": ip, "permessi": permessi,
            "via": "oauth" if tok.client_id != "codice-diretto" else "codice"}


def _registra(voce: dict):
    voce = {"quando": datetime.now(timezone.utc).isoformat(timespec="seconds"), **voce}
    try:
        with open(REGISTRO, "a") as f:
            f.write(json.dumps(voce, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(json.dumps({"connettore": voce}, ensure_ascii=False), flush=True)   # anche nel journal


STRUMENTI_RISTRETTI = ("riassunto", "container", "log", "risorse")


def _puo_container(perm: dict, nome: str) -> bool:
    return any(fnmatch.fnmatchcase(nome, g) for g in perm.get("container", []))


def _puo_unita(perm: dict, unita: str) -> bool:
    return any(fnmatch.fnmatchcase(unita, g) for g in perm.get("servizi", []))


def _uscita(ctx, strumento, argomenti, f, ristretto=None):
    """f() per root; ristretto(permessi) per i codici col profilo «ristretto» (vedono solo il loro)."""
    chi = _chi(ctx)
    t0 = time.time()
    if chi["profilo"] == "root":
        esegui = f
    elif chi["profilo"] == "ristretto" and ristretto is not None:
        esegui = lambda: ristretto(chi.get("permessi") or {})
    else:
        _registra({"chi": chi["nome"], "ip": chi["ip"], "strumento": strumento, "esito": "non permesso"})
        return ("Questo codice non può usare questo strumento. Può usare: " + ", ".join(STRUMENTI_RISTRETTI)
                + " (solo sui container che gli sono stati assegnati).")
    try:
        r = esegui()
        esito = "ok"
    except Exception as e:
        r, esito = f"Errore: {type(e).__name__}: {e}", "errore"
    testo = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, indent=1, default=str)
    testo = taglia(pulisci(testo))
    _registra({"chi": chi["nome"], "via": chi.get("via", ""), "ip": chi["ip"], "strumento": strumento, "argomenti": argomenti,
               "esito": esito, "ms": int((time.time() - t0) * 1000), "caratteri": len(testo)})
    return testo


# ---------------------------------------------------------------- fonti

def _stato_host() -> dict:
    with open(STATO) as f:
        d = json.load(f)
    eta = (datetime.now(timezone.utc) - datetime.fromisoformat(d["raccolto_utc"])).total_seconds()
    d["eta_secondi"] = int(eta)
    return d


def _durata(secondi: float) -> str:
    s = int(secondi)
    g, s = divmod(s, 86400); o, s = divmod(s, 3600); m, s = divmod(s, 60)
    return f"{g}g {o}h" if g else f"{o}h {m}m" if o else f"{m}m {s}s"


def _iso_docker(iso: str) -> float:
    if not iso or iso.startswith("0001"):
        return 0
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)", iso)
    if not m:
        return 0
    return datetime.fromisoformat(m.group(1) + (m.group(3).replace("Z", "+00:00"))).timestamp()


def _containers(dettaglio_per=None) -> list:
    """Dal raccoglitore (docker inspect come root, a ogni giro): qui dentro niente socket Docker."""
    d = _stato_host().get("docker")
    if not isinstance(d, list):
        raise RuntimeError(f"container non disponibili dal raccoglitore: {d}")
    ora = time.time()
    out = []
    for c in d:
        if dettaglio_per and c["nome"] != dettaglio_per:
            continue
        v = dict(c)
        v["salute"] = c["salute"] or "-"
        v["da"] = _durata(ora - _iso_docker(c["partito"])) if c["acceso"] else "fermo dal " + c["finito"][:19]
        for k in ("partito", "finito", "acceso"):
            v.pop(k, None)
        if not dettaglio_per:
            for k in ("creato", "comando", "utente", "sola_lettura", "limiti", "montaggi", "ultimi_controlli_salute"):
                v.pop(k, None)
        out.append(v)
    return sorted(out, key=lambda x: (x["stato"] != "running", x["nome"]))


def _journal(args: list, righe: int) -> str:
    r = subprocess.run(["journalctl", "-q", "--no-pager", "--utc", "-o", "short-iso", "-n", str(righe), *args],
                       capture_output=True, text=True, timeout=20)
    return r.stdout or r.stderr or "(nessuna riga)"


def _risorse() -> dict:
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    l1, l5, l15 = os.getloadavg()
    return {"cpu_core": psutil.cpu_count(), "cpu_uso_pct": psutil.cpu_percent(interval=0.5),
            "carico_1_5_15": [round(l1, 2), round(l5, 2), round(l15, 2)],
            "ram_gb": {"totale": round(vm.total / 2**30, 1), "usata": round((vm.total - vm.available) / 2**30, 1),
                       "uso_pct": vm.percent},
            "swap_gb": {"totale": round(sw.total / 2**30, 1), "usata": round(sw.used / 2**30, 1), "uso_pct": sw.percent},
            "acceso_da": _durata(time.time() - psutil.boot_time())}


def _processi(ordina="cpu", quanti=15) -> list:
    ps = list(psutil.process_iter(["pid", "name", "username", "memory_info", "cmdline", "create_time"]))
    for p in ps:
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.6)
    out = []
    for p in ps:
        try:
            cpu = p.cpu_percent(None)
            i = p.info
            out.append({"pid": i["pid"], "nome": i["name"], "utente": i["username"] or "",
                        "cpu_pct": round(cpu, 1), "ram_mb": (i["memory_info"].rss if i["memory_info"] else 0) // 2**20,
                        "da": _durata(time.time() - i["create_time"]),
                        "comando": " ".join(i["cmdline"] or [])[:160]})
        except Exception:
            pass
    chiave = "ram_mb" if ordina == "ram" else "cpu_pct"
    return sorted(out, key=lambda x: x[chiave], reverse=True)[:max(1, min(quanti, 50))]


def _llm() -> dict:
    out = {"servizi": [], "processi": []}
    for u in LLM_URLS:
        voce = {"indirizzo": u}
        for p in ("/v1/models", "/props", "/slots", "/api/ps", "/health"):
            try:
                with urllib.request.urlopen(u + p, timeout=3) as r:
                    t = r.read(20000).decode(errors="replace")
                try:
                    j = json.loads(t)
                except ValueError:
                    j = t[:300]
                if p == "/slots" and isinstance(j, list):
                    j = {"slot": len(j), "occupati": sum(1 for s in j if s.get("is_processing"))}
                if p == "/props" and isinstance(j, dict):
                    j = {k: j.get(k) for k in ("model_path", "total_slots", "build_info", "n_ctx") if k in j} or \
                        {"n_ctx": (j.get("default_generation_settings") or {}).get("n_ctx")}
                voce[p] = j
            except Exception as e:
                voce.setdefault("non_risponde", []).append(p)
        voce["acceso"] = len(voce.get("non_risponde", [])) < 5
        out["servizi"].append(voce)
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "create_time"]):
        try:
            c = " ".join(p.info["cmdline"] or [])
            if re.search(r"llama-server|llama\.cpp|ollama|vllm|text-generation|koboldcpp|lmstudio", c + " " + (p.info["name"] or ""), re.I):
                out["processi"].append({"pid": p.info["pid"], "nome": p.info["name"], "comando": c[:250],
                                        "ram_mb": p.info["memory_info"].rss // 2**20, "da": _durata(time.time() - p.info["create_time"])})
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- strumenti

def _container_ristretti(perm, dettaglio_per=None):
    cs = [c for c in _containers(dettaglio_per) if _puo_container(perm, c["nome"])]
    for c in cs:
        c.pop("montaggi", None)        # percorsi dell'host: non servono a chi vede solo il suo
    return cs


def _riassunto_ristretto(perm):
    righe = [f"# {NOME_MACCHINA} — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC (vista ristretta)"]
    avvisi = []
    if perm.get("macchina"):
        r = _risorse()
        righe.append(f"\n## Salute della macchina\nCPU {r['cpu_uso_pct']}% su {r['cpu_core']} core, carico "
                     f"{r['carico_1_5_15']}; RAM {r['ram_gb']['usata']}/{r['ram_gb']['totale']} GB ({r['ram_gb']['uso_pct']}%)")
        for d in _stato_host().get("dischi", []):
            if d["montato_su"] == "/":
                righe.append(f"Disco: {d['uso_pct']}% usato, liberi {d['libero_gb']} GB")
                if d["uso_pct"] >= 85:
                    avvisi.append(f"disco al {d['uso_pct']}%")
    cs = _container_ristretti(perm)
    righe.append(f"\n## I tuoi container: {sum(1 for c in cs if c['stato'] == 'running')} accesi su {len(cs)}")
    for c in cs:
        righe.append(f"- {c['nome']}: {c['stato']}, {c['da']}, salute {c['salute']}"
                     + (f", {c['riavvii']} riavvii" if c["riavvii"] else ""))
        if c["stato"] != "running" or c["salute"] == "unhealthy":
            avvisi.append(f"{c['nome']} {c['stato']} {c['salute']}")
    if not cs:
        righe.append("(nessun container assegnato a questo codice)")
    righe.append("\n## Avvisi\n" + ("\n".join("⚠ " + a for a in avvisi) if avvisi else "nessuno"))
    return "\n".join(righe)


def _log_ristretto(perm, dove, n, extra):
    if _puo_container(perm, dove):
        return _journal([f"CONTAINER_NAME={dove}", *extra], n)
    unita = dove if "." in dove else dove + ".service"
    if _puo_unita(perm, unita):
        return _journal(["-u", unita, *extra], n)
    return f"«{dove}» non è tra quelli che questo codice può vedere."


def _risorse_ristrette(perm):
    if not perm.get("macchina"):
        return "Questo codice non vede la salute della macchina."
    return {**_risorse(), "dischi": [{k: d[k] for k in ("montato_su", "uso_pct", "libero_gb", "totale_gb")}
                                     for d in _stato_host().get("dischi", [])]}


@mcp.tool()
def riassunto(ctx: Context) -> str:
    """Quadro completo della macchina in una volta: salute (CPU, RAM, dischi, carico), container Docker (fermi,
    malati, riavvii), servizi falliti, chi è dentro in SSH adesso e chi è entrato nelle ultime 24 ore, Claude Code
    lanciati (cartella, da quanto, cosa sta eseguendo), ultimi accessi a Claude Code, tutti gli utenti (chi è dentro,
    ultimo accesso, processi, Claude), lavori aperti in tmux, LLM locale, porte esposte, avvisi.
    Usalo per primo per domande come «come sta il server», «cosa sta facendo», «c'è qualcuno dentro»."""
    def f():
        h = _stato_host()
        r = _risorse()
        avvisi = []
        righe = [f"# {NOME_MACCHINA} — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"]
        m = h.get("macchina", {})
        righe.append(f"{m.get('sistema', '')}, kernel {m.get('kernel', '')}, acceso da {r['acceso_da']}. "
                     f"Aggiornamenti in attesa: {m.get('aggiornamenti_in_attesa', '?')}"
                     + (", RIAVVIO RICHIESTO" if m.get("riavvio_richiesto") else ""))
        righe.append(f"\n## Salute\nCPU {r['cpu_uso_pct']}% su {r['cpu_core']} core, carico {r['carico_1_5_15']}; "
                     f"RAM {r['ram_gb']['usata']}/{r['ram_gb']['totale']} GB ({r['ram_gb']['uso_pct']}%); "
                     f"swap {r['swap_gb']['usata']}/{r['swap_gb']['totale']} GB")
        if r["ram_gb"]["uso_pct"] > 90:
            avvisi.append(f"RAM al {r['ram_gb']['uso_pct']}%")
        if r["carico_1_5_15"][1] > r["cpu_core"] * 1.5:
            avvisi.append(f"carico alto ({r['carico_1_5_15'][1]})")
        for d in h.get("dischi", []):
            righe.append(f"Disco {d['montato_su']}: {d['usato_gb']}/{d['totale_gb']} GB ({d['uso_pct']}%), liberi {d['libero_gb']} GB")
            if d["uso_pct"] >= 85:
                avvisi.append(f"disco {d['montato_su']} al {d['uso_pct']}%")
        if h.get("gpu"):
            for g in h["gpu"]:
                righe.append(f"GPU {g['nome']}: {g['uso_pct']}%, memoria {g['mem_usata_mb']}/{g['mem_totale_mb']} MB, {g['temp_c']}°C")

        try:
            cs = _containers()
            su = [c for c in cs if c["stato"] == "running"]
            righe.append(f"\n## Container: {len(su)} accesi su {len(cs)}")
            for c in cs:
                problemi = []
                if c["stato"] != "running":
                    problemi.append(c["stato"])
                if c["salute"] not in ("-", "healthy"):
                    problemi.append(c["salute"])
                if c["riavvii"]:
                    problemi.append(f"{c['riavvii']} riavvii")
                righe.append(f"- {c['nome']}: {c['stato']}, {c['da']}, salute {c['salute']}"
                             + (f", porte {', '.join(c['porte_pubblicate'])}" if c["porte_pubblicate"] else "")
                             + (f"  ⚠ {', '.join(problemi)}" if problemi and c["politica_riavvio"] != "no" else ""))
                if problemi and c["stato"] in ("restarting",) or c["salute"] == "unhealthy":
                    avvisi.append(f"container {c['nome']} {', '.join(problemi)}")
        except Exception as e:
            righe.append(f"\n## Container: non leggibili ({e})")

        sv = h.get("servizi", {})
        righe.append(f"\n## Servizi systemd: {len(sv.get('in_esecuzione', []))} in esecuzione, "
                     f"{len(sv.get('falliti', []))} falliti"
                     + (": " + ", ".join(x["unita"] for x in sv.get("falliti", [])) if sv.get("falliti") else ""))
        for x in sv.get("falliti", []):
            avvisi.append(f"servizio fallito {x['unita']}")

        a = h.get("accessi", {})
        ssh = [d for d in a.get("dentro_adesso", []) if d.get("ssh")]
        locali = [d for d in a.get("dentro_adesso", []) if not d.get("ssh")]
        righe.append(f"\n## Chi è dentro\nSSH adesso: {len(ssh)}"
                     + "".join(f"\n- {d['utente']} da {d['da']} ({d['terminale']}, dalle {d['dal_ora_locale']} ora locale)" for d in ssh))
        if locali:
            righe.append("Console/schermo: " + ", ".join(sorted({f"{d['utente']} ({d['terminale']})" for d in locali})))
        ent = a.get("entrati_24h", [])
        if ent:
            ult = {}
            for e in ent:
                ult[(e["utente"], e["da"])] = (ult.get((e["utente"], e["da"]), (0, ""))[0] + 1, e["quando"])
            righe.append("Entrati in SSH nelle ultime 24 ore: " + "; ".join(
                f"{u} da {ip} ×{n} (ultimo {q[:16]})" for (u, ip), (n, q) in ult.items()))
        righe.append(f"Tentativi rifiutati 24 ore: {a.get('rifiutati_24h', 0)} da {a.get('rifiutati_ip_diversi_24h', 0)} IP")

        cl = h.get("claude", [])
        righe.append(f"\n## Claude Code lanciati: {len(cl)}")
        for c in cl:
            t = c.get("tmux") or {}
            righe.append(f"- {c['utente']} in {c['cartella']}, da {_durata(c['da_secondi'])}"
                         + (f", tmux «{t['sessione']}»{' (qualcuno la guarda)' if t.get('attaccata') else ''}" if t else "")
                         + (f", sta eseguendo: {', '.join(c['sta_eseguendo'])}" if c["sta_eseguendo"] else ", fermo in attesa")
                         + f", RAM {c['ram_mb']} MB")
        cs = h.get("claude_sessioni") or []
        if isinstance(cs, list) and cs:
            righe.append("\n## Ultimi accessi a Claude Code (da terminale)")
            for x in cs[:6]:
                righe.append(f"- {x['utente']} in {x['progetto']}: ultimo messaggio {x['ultimo_utc'][:16]} "
                             f"({x['messaggi']} messaggi dal {x['inizio_utc'][:10]}) — «{x['ultimo_messaggio'][:80]}»")

        ut = h.get("utenti") or []
        if isinstance(ut, list):
            righe.append(f"\n## Utenti ({len(ut)})")
            for u in ut:
                acc = u["ultimi_accessi"][0] if u["ultimi_accessi"] else None
                righe.append(f"- {u['utente']}{' [' + ', '.join(u['gruppi_potenti']) + ']' if u['gruppi_potenti'] else ''}: "
                             + (f"DENTRO ({len(u['dentro_adesso'])} sessioni)" if u["dentro_adesso"] else "fuori")
                             + (f", ultimo accesso {acc['inizio_utc'][:16]} da {acc['da']}" if acc else ", mai entrato")
                             + f", {u['processi']} processi ({u['ram_mb']} MB)"
                             + (f", Claude lanciati {u['claude_lanciati']}" if u["claude_lanciati"] else "")
                             + (f", Claude usato l'ultima volta {u['claude_ultimo_uso_utc'][:16]} in {u['claude_ultimo_progetto']}" if u["claude_ultimo_uso_utc"] else "")
                             + (f", {u['memorie_file']} file di memoria" if u["memorie_file"] else ""))

        lv = h.get("lavori", {})
        aperti = [l for l in lv.get("indice", []) if l.get("aperto_in_tmux")]
        righe.append(f"\n## Lavori: {len(lv.get('indice', []))} nell'indice, {len(aperti)} aperti in tmux")
        for l in sorted(lv.get("indice", []), key=lambda x: x.get("ultimo_uso", ""), reverse=True)[:6]:
            righe.append(f"- {l['nome']}{' (APERTO)' if l.get('aperto_in_tmux') else ''}: {l['descrizione'][:90]} — ultimo uso {l['ultimo_uso']}")

        try:
            ll = _llm()
            accesi = [s for s in ll["servizi"] if s["acceso"]]
            righe.append(f"\n## LLM locale: {'acceso' if accesi or ll['processi'] else 'spento'}")
            for s in accesi:
                mod = s.get("/v1/models")
                nomi = [x.get("id") for x in mod.get("data", [])] if isinstance(mod, dict) else []
                sl = s.get("/slots") or {}
                righe.append(f"- {s['indirizzo']}: modelli {nomi or '?'}"
                             + (f", richieste in corso {sl.get('occupati')}/{sl.get('slot')}" if sl else ""))
            for p in ll["processi"][:4]:
                righe.append(f"- processo {p['nome']} (pid {p['pid']}), RAM {p['ram_mb']} MB, da {p['da']}")
        except Exception as e:
            righe.append(f"\n## LLM: non leggibile ({e})")

        aperte = [p for p in h.get("porte", []) if not p["solo_locale"]]
        righe.append(f"\n## Porte aperte verso fuori ({len(aperte)} socket): (" + ", ".join(
            sorted({f"{p['proto']} {p['indirizzo'].rsplit(':', 1)[-1]} {p['programma']}".strip() for p in aperte},
                   key=lambda s: (s.split()[0], int(s.split()[1]) if s.split()[1].isdigit() else 0))) + ")")

        righe.append("\n## Avvisi\n" + ("\n".join("⚠ " + x for x in avvisi) if avvisi else "nessuno"))
        righe.append(f"\n(fotografia del raccoglitore di {h['eta_secondi']} secondi fa)")
        if h["eta_secondi"] > 2 * OGNI_MINUTI * 60 + 60:
            righe.append(f"⚠ il raccoglitore non aggiorna da {_durata(h['eta_secondi'])} (dovrebbe ogni {OGNI_MINUTI} minuti): "
                         "lavori, SSH, servizi, container e utenti sono vecchi")
        return "\n".join(righe)
    return _uscita(ctx, "riassunto", {}, f, _riassunto_ristretto)


@mcp.tool()
def container(ctx: Context, nome: str = "") -> str:
    """Container Docker: senza nome l'elenco di tutti (stato, da quanto, salute, riavvii, porte); con un nome il
    dettaglio (comando, utente, limiti, montaggi, ultimi controlli di salute)."""
    return _uscita(ctx, "container", {"nome": nome}, lambda: _containers(nome or None) or f"Nessun container «{nome}».",
                   lambda perm: _container_ristretti(perm, nome or None) or f"Nessun container «{nome}» tra i tuoi.")


@mcp.tool()
def log(ctx: Context, dove: str, righe: int = 50, cerca: str = "", da_minuti: int = 0) -> str:
    """Ultime righe di log (max 200) di un container Docker (nome) o di un servizio systemd (es. «ssh», «docker»,
    «fail2ban.service»). `cerca` filtra (espressione regolare), `da_minuti` limita al periodo. Segreti nascosti."""
    n = max(1, min(int(righe), MAX_RIGHE))
    extra = ["--grep", cerca] if cerca else []
    if da_minuti:
        extra += ["--since", f"-{int(da_minuti)}min"]
    valido = bool(re.fullmatch(r"[\w.@:-]{1,80}", dove))

    def f():
        if not valido:
            return "Nome non valido."
        nomi = {c["nome"] for c in (_stato_host().get("docker") or [])}
        if dove in nomi:
            return _journal([f"CONTAINER_NAME={dove}", *extra], n)
        unita = dove if "." in dove else dove + ".service"
        return _journal(["-u", unita, *extra], n)
    return _uscita(ctx, "log", {"dove": dove, "righe": righe, "cerca": cerca, "da_minuti": da_minuti}, f,
                   lambda perm: _log_ristretto(perm, dove, n, extra) if valido else "Nome non valido.")


@mcp.tool()
def servizi(ctx: Context) -> str:
    """Servizi systemd in esecuzione e falliti, e i timer (prossimo e ultimo giro)."""
    def f():
        s = _stato_host()["servizi"]
        for t in s.get("timer", []):
            for k in ("prossimo_us", "ultimo_us"):
                v = t.pop(k, None)
                t[k.replace("_us", "")] = datetime.fromtimestamp(v / 1e6, timezone.utc).isoformat(timespec="minutes") if v else "-"
        return s
    return _uscita(ctx, "servizi", {}, f)


@mcp.tool()
def processi(ctx: Context, ordina: str = "cpu", quanti: int = 15) -> str:
    """Processi principali della macchina ordinati per «cpu» o «ram» (max 50)."""
    return _uscita(ctx, "processi", {"ordina": ordina, "quanti": quanti}, lambda: _processi(ordina, quanti))


@mcp.tool()
def risorse(ctx: Context) -> str:
    """CPU, carico, RAM, swap, dischi e GPU (se c'è)."""
    def f():
        h = _stato_host()
        return {**_risorse(), "dischi": h.get("dischi"), "gpu": h.get("gpu")}
    return _uscita(ctx, "risorse", {}, f, _risorse_ristrette)


@mcp.tool()
def accessi(ctx: Context) -> str:
    """Chi è dentro adesso (SSH e console) e gli accessi SSH delle ultime 24 ore, con i tentativi rifiutati."""
    return _uscita(ctx, "accessi", {}, lambda: _stato_host()["accessi"])


@mcp.tool()
def claude_e_lavori(ctx: Context) -> str:
    """Sessioni di Claude Code lanciate sulla macchina (utente, cartella, da quanto, sessione tmux, cosa stanno
    eseguendo adesso) e i lavori dell'indice `lavori` con le sessioni tmux aperte."""
    def f():
        h = _stato_host()
        return {"claude": h.get("claude"), "lavori": h.get("lavori"), "fotografia_di_secondi_fa": h["eta_secondi"]}
    return _uscita(ctx, "claude_e_lavori", {}, f)


@mcp.tool()
def utenti(ctx: Context, nome: str = "") -> str:
    """Tutti gli utenti veri della macchina (root e uid >= 1000): se sono dentro adesso, ultimi accessi (da dove,
    quando, quanto), processi e RAM, gruppi potenti (sudo, docker), Claude Code lanciato e usato, file di memoria.
    Con `nome` solo quell'utente."""
    def f():
        u = _stato_host().get("utenti") or []
        return [x for x in u if not nome or x["utente"] == nome] or f"Nessun utente «{nome}»."
    return _uscita(ctx, "utenti", {"nome": nome}, f)


@mcp.tool()
def claude_sessioni(ctx: Context, utente: str = "", progetto: str = "", quante: int = 20) -> str:
    """Ultimi accessi a Claude Code da terminale (da ~/.claude/history.jsonl di tutti gli utenti, ultimi 14 giorni):
    per sessione utente, cartella del progetto, inizio, ultimo messaggio, quanti messaggi e il testo dell'ultimo."""
    def f():
        cs = _stato_host().get("claude_sessioni") or []
        cs = [x for x in cs if (not utente or x["utente"] == utente) and (not progetto or progetto in x["progetto"])]
        return cs[:max(1, min(int(quante), 60))]
    return _uscita(ctx, "claude_sessioni", {"utente": utente, "progetto": progetto, "quante": quante}, f)


@mcp.tool()
def memorie(ctx: Context, progetto: str = "", file: str = "", utente: str = "") -> str:
    """Memorie di Claude Code (~/.claude/projects/<progetto>/memory, più ~/.claude/CLAUDE.md «(globale)») di tutti
    gli utenti. Senza argomenti: l'elenco dei progetti. Con `progetto` (anche solo un pezzo del nome, es. «progetto»):
    l'indice MEMORY.md e i file. Con `progetto` e `file`: il testo di quel ricordo."""
    def f():
        with open(os.path.join(os.path.dirname(STATO), "memorie.json")) as fh:
            m = json.load(fh).get("memorie", [])
        m = [x for x in m if not utente or x["utente"] == utente]
        if not progetto:
            return [{"utente": x["utente"], "progetto": x["progetto"], "file": len(x["file"]),
                     "aggiornato_utc": x["aggiornato_utc"]} for x in m]
        trovati = [x for x in m if x["progetto"] == progetto] or [x for x in m if progetto.lower() in x["progetto"].lower()]
        if not trovati:
            return f"Nessun progetto «{progetto}»."
        if len(trovati) > 1 and not file:
            return {"piu_progetti": [f"{x['utente']}: {x['progetto']}" for x in trovati]}
        x = trovati[0]
        if file:
            for fl in x["file"]:
                if fl["nome"] == file or fl["nome"] == file + ".md":
                    return f"# {x['progetto']}/{fl['nome']} (aggiornato {fl['aggiornato_utc']})\n\n{fl['testo']}"
            return f"Nessun file «{file}» in {x['progetto']}."
        indice = next((fl["testo"] for fl in x["file"] if fl["nome"] == "MEMORY.md"), "")
        return {"utente": x["utente"], "progetto": x["progetto"], "indice_MEMORY_md": indice,
                "file": [{"nome": fl["nome"], "byte": fl["byte"], "aggiornato_utc": fl["aggiornato_utc"]} for fl in x["file"]]}
    return _uscita(ctx, "memorie", {"progetto": progetto, "file": file, "utente": utente}, f)


@mcp.tool()
def porte(ctx: Context, solo_esterne: bool = True) -> str:
    """Porte in ascolto (tcp/udp) con il programma; di base solo quelle raggiungibili da fuori."""
    def f():
        p = _stato_host()["porte"]
        return [x for x in p if not (solo_esterne and x["solo_locale"])]
    return _uscita(ctx, "porte", {"solo_esterne": solo_esterne}, f)


@mcp.tool()
def llm(ctx: Context) -> str:
    """LLM locale: se risponde, modelli caricati, richieste in corso (slot), e i processi tipo llama-server/ollama."""
    return _uscita(ctx, "llm", {}, _llm)


# ---------------------------------------------------------------- pagina dove si incolla il codice (OAuth)

def _rifiutato(ip, percorso):
    print(json.dumps({"connettore": {"rifiutato": ip, "percorso": re.sub(r"/c/[^/]*", "/c/…", percorso)[:60]}}), flush=True)


@mcp.custom_route("/accedi", methods=["GET", "POST"])
async def accedi(request: Request):
    testo = "Incolla il tuo codice del connettore per collegare Claude a questa macchina (sola lettura)."
    if request.method == "GET":
        rid = request.query_params.get("r", "")
        if not FORNITORE.richiesta(rid):
            return HTMLResponse(pagina(NOME_MACCHINA, "Richiesta scaduta o non valida: riparti da claude.ai."), 400)
        return HTMLResponse(pagina(NOME_MACCHINA, testo, rid=rid))
    corpo = (await request.body())[:4096].decode(errors="replace")
    campi = {k: v[0] for k, v in urllib.parse.parse_qs(corpo).items()}
    rid, codice = campi.get("r", ""), campi.get("codice", "")
    ip = request.headers.get("x-connettore-ip", "")
    ritorno, chi = FORNITORE.completa(rid, codice)
    if not ritorno:
        if chi == "Codice sbagliato o spento.":
            _rifiutato(ip, "/accedi")            # conta per fail2ban come un codice sbagliato
        return HTMLResponse(pagina(NOME_MACCHINA, testo, errore=chi, rid=rid if FORNITORE.richiesta(rid) else ""), 403)
    _registra({"chi": chi, "via": "oauth", "ip": ip, "strumento": "(collegamento OAuth)", "esito": "ok"})
    return RedirectResponse(ritorno, status_code=302)


# ---------------------------------------------------------------- avvio

PERCORSI_OAUTH = ("/mcp", "/mcp/", "/authorize", "/token", "/register", "/revoke", "/accedi")


def app():
    interna = mcp.streamable_http_app()

    async def risposta_404(send):
        await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"404"})

    async def entrata(scope, receive, send):
        if scope["type"] == "lifespan":
            return await interna(scope, receive, send)
        if scope["type"] != "http":
            return
        ip = (scope.get("client") or ("?", 0))[0]
        percorso = scope.get("path", "")
        intestazioni = [(k, v) for k, v in scope["headers"] if not k.startswith(b"x-connettore-")]
        intestazioni.append((b"x-connettore-ip", ip.encode()))
        if percorso in ("/favicon.ico", "/robots.txt"):
            return await risposta_404(send)
        if percorso.startswith("/.well-known/") or percorso in PERCORSI_OAUTH:
            # OAuth e metadati: li gestisce la libreria (niente fail2ban sui 404 di scoperta)
            return await interna({**scope, "headers": intestazioni}, receive, send)
        m = re.fullmatch(r"/c/([A-Za-z0-9_-]{16,128})/mcp/?", percorso)
        if not m or not FORNITORE.voce_codice(impronta(m.group(1))):
            _rifiutato(ip, percorso)
            return await risposta_404(send)
        # /c/<codice>/mcp: il codice diventa un token interno, poi passa dallo stesso controllo dell'OAuth
        intestazioni = [(k, v) for k, v in intestazioni if k != b"authorization"]
        intestazioni.append((b"authorization", f"Bearer {FORNITORE.token_interno(m.group(1))}".encode()))
        return await interna({**scope, "path": "/mcp", "raw_path": b"/mcp", "headers": intestazioni}, receive, send)

    return entrata


if __name__ == "__main__":
    import uvicorn
    # access_log spento: gli indirizzi possono contenere il codice. Le chiamate le registra _registra(), senza codice.
    uvicorn.run(app(), host=HOST, port=PORTA, server_header=False, proxy_headers=False, access_log=False,
                ssl_certfile=os.environ.get("TLS_CERT") or None, ssl_keyfile=os.environ.get("TLS_KEY") or None)
