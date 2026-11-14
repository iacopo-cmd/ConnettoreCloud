#!/usr/bin/env python3
"""Raccoglitore del Connettore cloud: gira sul server come root (timer, di base ogni 5 minuti al secondo 15), SOLO LETTURA.

Raccoglie quello che il container MCP non può vedere da solo e lo scrive in un file JSON:
lavori (indice di `lavori` e sessioni tmux), Claude lanciati (cartella, da quanto, se sta lavorando),
chi è dentro in SSH/console, ultimi accessi SSH, porte in ascolto, servizi systemd, dischi, aggiornamenti.
Solo libreria standard. Non cambia niente sul server.
"""
import json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timezone

USCITA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dati", "stato-host.json")
GRUPPO = int(os.environ.get("CONNETTORE_UID", "10110"))   # gruppo del container: può leggere il file
TICK = os.sysconf("SC_CLK_TCK")


def cmd(*a, t=15):
    try:
        r = subprocess.run(a, capture_output=True, text=True, timeout=t)
        return r.stdout
    except Exception as e:  # il raccoglitore non deve mai fermarsi per un pezzo rotto
        return ""


def avvio_sistema():
    with open("/proc/stat") as f:
        for r in f:
            if r.startswith("btime"):
                return int(r.split()[1])
    return 0


BTIME = avvio_sistema()


def proc(pid):
    """Dati di un processo da /proc (None se sparito)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
        nome = s[s.index("(") + 1:s.rindex(")")]
        campi = s[s.rindex(")") + 2:].split()
        ppid = int(campi[1])
        cpu_s = (int(campi[11]) + int(campi[12])) / TICK
        inizio = BTIME + int(campi[19]) / TICK
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = [x.decode(errors="replace") for x in f.read().split(b"\0") if x]
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = ""
        rss = 0
        with open(f"/proc/{pid}/status") as f:
            for r in f:
                if r.startswith("VmRSS:"):
                    rss = int(r.split()[1]) * 1024
                if r.startswith("Uid:"):
                    uid = int(r.split()[1])
        tty = os.readlink(f"/proc/{pid}/fd/0") if os.path.exists(f"/proc/{pid}/fd/0") else ""
        return {"pid": pid, "ppid": ppid, "nome": nome, "argv": argv, "cwd": cwd, "cpu_s": round(cpu_s, 1),
                "inizio": inizio, "rss": rss, "uid": uid, "tty": tty if tty.startswith("/dev/pts") else ""}
    except Exception:
        return None


def tutti_i_processi():
    out = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            p = proc(int(d))
            if p:
                out[p["pid"]] = p
    return out


def utente(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except Exception:
        return str(uid)


def claude_lanciati(procs):
    """Ogni processo `claude` (Claude Code): cartella, da quanto, opzioni, sessione tmux, se sta lavorando."""
    figli = {}
    for p in procs.values():
        figli.setdefault(p["ppid"], []).append(p)

    def discendenti(pid):
        out, coda = [], list(figli.get(pid, []))
        while coda:
            c = coda.pop()
            out.append(c)
            coda.extend(figli.get(c["pid"], []))
        return out

    # sessione tmux: risalgo i padri fino al server tmux e lo abbino con `tmux ls`
    tmux_pid_sessione = {}
    for r in cmd("tmux", "list-panes", "-a", "-F", "#{pane_pid}\t#{session_name}\t#{session_attached}").splitlines():
        x = r.split("\t")
        if len(x) == 3 and x[0].isdigit():
            tmux_pid_sessione[int(x[0])] = {"sessione": x[1], "attaccata": x[2] != "0"}

    ora = time.time()
    out = []
    for p in procs.values():
        base = os.path.basename(p["argv"][0]) if p["argv"] else ""
        if p["nome"] != "claude" and base != "claude":
            continue
        # salto i claude figli di un altro claude (sotto-agenti): li conto sotto il padre
        padre = procs.get(p["ppid"])
        if padre and (padre["nome"] == "claude"):
            continue
        sess, q = None, p
        for _ in range(8):
            if q["pid"] in tmux_pid_sessione:
                sess = tmux_pid_sessione[q["pid"]]
                break
            q = procs.get(q["ppid"])
            if not q:
                break
        disc = discendenti(p["pid"])
        comandi = [d for d in disc if d["nome"] not in ("claude",) and (ora - d["inizio"]) < 3600]
        out.append({
            "pid": p["pid"], "utente": utente(p["uid"]), "cartella": p["cwd"],
            "opzioni": [a for a in p["argv"][1:] if a.startswith("-")][:6],
            "partito_utc": datetime.fromtimestamp(p["inizio"], timezone.utc).isoformat(timespec="seconds"),
            "da_secondi": int(ora - p["inizio"]), "cpu_secondi_totali": p["cpu_s"], "ram_mb": p["rss"] // 2**20,
            "tmux": sess, "terminale": p["tty"],
            "sta_eseguendo": [os.path.basename(c["argv"][0]) if c["argv"] else c["nome"] for c in comandi][:8],
            "sotto_processi": len(disc),
        })
    return sorted(out, key=lambda x: x["da_secondi"])


def lavori():
    """Indice del comando `lavori` (/etc/lavori/indice.tsv) + sessioni tmux."""
    out = []
    try:
        with open("/etc/lavori/indice.tsv") as f:
            righe = [r.rstrip("\n").split("\t") for r in f if r.strip() and not r.startswith("#")]
        for x in righe:
            x += [""] * 6
            out.append({"nome": x[0], "sessione_tmux": x[1], "cartella": x[2], "creato": x[3],
                        "ultimo_uso": x[4], "descrizione": x[5]})
    except OSError:
        pass
    tmux = []
    for r in cmd("tmux", "ls", "-F", "#{session_name}\t#{session_created}\t#{session_attached}\t#{session_activity}\t#{session_windows}").splitlines():
        x = r.split("\t")
        if len(x) == 5:
            tmux.append({"sessione": x[0], "creata_utc": datetime.fromtimestamp(int(x[1]), timezone.utc).isoformat(timespec="seconds"),
                         "attaccata": x[2] != "0", "ultima_attivita_utc": datetime.fromtimestamp(int(x[3]), timezone.utc).isoformat(timespec="seconds"),
                         "finestre": int(x[4])})
    attive = {t["sessione"] for t in tmux}
    for l in out:
        l["aperto_in_tmux"] = l["sessione_tmux"] in attive
    return {"indice": out, "tmux": tmux}


def accessi():
    """Chi è dentro adesso (who/loginctl) e ultimi accessi SSH dal registro di sshd (24 ore)."""
    dentro = []
    for r in cmd("who").splitlines():
        m = re.match(r"^(\S+)\s+(.*?)\s+(\d{4}-\d\d-\d\d \d\d:\d\d)\s*(?:\(([^)]*)\))?", r)
        if m:
            da = m.group(4) or ""
            dentro.append({"utente": m.group(1), "terminale": m.group(2), "dal_ora_locale": m.group(3),
                           "da": da, "ssh": bool(da) and not da.startswith(":")})
    ultimi = []
    for r in cmd("journalctl", "-q", "--utc", "--since", "-24h", "-o", "short-iso", "_COMM=sshd", "_COMM=sshd-session", "--grep",
                 "Accepted|Failed password|Invalid user", "--no-pager").splitlines()[-400:]:
        m = re.search(r"^(\S+) .*?(Accepted|Failed password|Invalid user) (?:\w+ for )?(?:invalid user )?(\S+) from (\S+)", r)
        if m:
            ultimi.append({"quando": m.group(1), "esito": "entrato" if m.group(2) == "Accepted" else "rifiutato",
                           "utente": m.group(3), "da": m.group(4)})
    ok = [u for u in ultimi if u["esito"] == "entrato"]
    no = [u for u in ultimi if u["esito"] != "entrato"]
    return {"dentro_adesso": dentro, "entrati_24h": ok[-30:], "rifiutati_24h": len(no),
            "rifiutati_ip_diversi_24h": len({u["da"] for u in no})}


def porte():
    tcp = [{"proto": "tcp", **p} for p in _ss("-Htlnp")]
    udp = [{"proto": "udp", **p} for p in _ss("-Hulnp")]
    return tcp + udp


def _ss(flag):
    out = []
    for r in cmd("ss", flag).splitlines():
        x = r.split()
        if len(x) < 5:
            continue
        prog = re.search(r'users:\(\("([^"]+)"', r)
        ind = x[3]
        out.append({"indirizzo": ind, "programma": prog.group(1) if prog else "",
                    "solo_locale": ind.startswith(("127.", "[::1]", "::1", "%lo"))})
    return out


def servizi():
    attivi, falliti = [], []
    for r in cmd("systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager").splitlines():
        x = r.split(None, 4)
        if len(x) < 4:
            continue
        u = {"unita": x[0], "carico": x[1], "attivo": x[2], "stato": x[3], "descrizione": x[4] if len(x) > 4 else ""}
        if x[2] == "failed":
            falliti.append(u)
        elif x[2] == "active" and x[3] == "running":
            attivi.append(u)
    timer = []
    for r in cmd("systemctl", "list-timers", "--all", "--no-legend", "--no-pager", "-o", "json").splitlines()[:1]:
        try:
            for t in json.loads(r):
                timer.append({"unita": t.get("unit"), "attiva": t.get("activates"),
                              "prossimo_us": t.get("next"), "ultimo_us": t.get("last")})
        except Exception:
            pass
    return {"in_esecuzione": attivi, "falliti": falliti, "timer": timer}


def dischi():
    out = []
    for r in cmd("df", "-PT", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay", "-x", "squashfs", "-x", "efivarfs").splitlines()[1:]:
        x = r.split()
        if len(x) >= 7:
            out.append({"dispositivo": x[0], "tipo": x[1], "totale_gb": round(int(x[2]) / 2**20, 1),
                        "usato_gb": round(int(x[3]) / 2**20, 1), "libero_gb": round(int(x[4]) / 2**20, 1),
                        "uso_pct": int(x[5].rstrip("%")), "montato_su": x[6]})
    return out


def gpu():
    if not shutil.which("nvidia-smi"):
        return None
    r = cmd("nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits")
    return [dict(zip(["nome", "uso_pct", "mem_usata_mb", "mem_totale_mb", "temp_c"], [c.strip() for c in l.split(",")])) for l in r.splitlines()]


def docker():
    """Container Docker letti col comando docker (il connettore non ha il socket Docker).
    Si tengono solo i campi che servono: MAI le variabili d'ambiente (lì ci sono le chiavi)."""
    if not shutil.which("docker"):
        return None
    ids = cmd("docker", "ps", "-aq").split()
    if not ids:
        return []
    try:
        tutti = json.loads(cmd("docker", "inspect", *ids, t=30) or "[]")
    except ValueError:
        return {"errore": "docker inspect non leggibile"}
    out = []
    for i in tutti:
        hc, cf, ns = i.get("HostConfig") or {}, i.get("Config") or {}, i.get("NetworkSettings") or {}
        st = i.get("State") or {}
        porte = []
        for interna, est in (ns.get("Ports") or {}).items():
            for e in est or []:
                porte.append(f"{e.get('HostIp', '')}:{e.get('HostPort')}->{interna.split('/')[0]}")
        out.append({
            "nome": i.get("Name", "").lstrip("/"), "immagine": cf.get("Image", ""),
            "stato": st.get("Status"), "acceso": st.get("Running"), "salute": (st.get("Health") or {}).get("Status", ""),
            "partito": st.get("StartedAt", ""), "finito": st.get("FinishedAt", ""), "uscita": st.get("ExitCode"),
            "riavvii": i.get("RestartCount", 0), "politica_riavvio": (hc.get("RestartPolicy") or {}).get("Name", ""),
            "porte_pubblicate": sorted(set(porte)),
            "reti": sorted((ns.get("Networks") or {}).keys()) or [hc.get("NetworkMode")],
            "compose": (cf.get("Labels") or {}).get("com.docker.compose.project", ""),
            "creato": i.get("Created", "")[:19], "comando": " ".join(cf.get("Cmd") or [])[:300],
            "utente": cf.get("User") or "root", "sola_lettura": hc.get("ReadonlyRootfs"),
            "limiti": {"ram_mb": (hc.get("Memory") or 0) // 2**20, "cpu": (hc.get("NanoCpus") or 0) / 1e9},
            "montaggi": [f"{m.get('Source', '')} -> {m.get('Destination')} ({'rw' if m.get('RW') else 'ro'})" for m in i.get("Mounts", [])],
            "ultimi_controlli_salute": [{"quando": h.get("Start", "")[:19], "esito": h.get("ExitCode"), "output": (h.get("Output") or "")[-200:]}
                                        for h in ((st.get("Health") or {}).get("Log") or [])[-3:]],
        })
    return out


def utenti_veri():
    """Root e tutti gli utenti umani (uid >= 1000), ovunque stia la loro home."""
    import pwd
    out = []
    for u in pwd.getpwall():
        if u.pw_uid == 0 or (1000 <= u.pw_uid < 60000):
            out.append(u)
    return out


def _case_claude():
    """Cartelle ~/.claude di root e di TUTTI gli utenti veri."""
    out, viste = [], set()
    for u in utenti_veri():
        c = os.path.join(u.pw_dir, ".claude")
        if os.path.isdir(c) and c not in viste:
            viste.add(c)
            out.append((u.pw_name, c))
    return out


def utenti(procs, acc, sessioni, mem_conteggi):
    """Una scheda per ogni utente vero: dentro adesso, ultimi accessi (wtmpdb), processi, Claude, memorie, gruppi."""
    import grp
    accessi_per = {}
    for r in cmd("wtmpdb", "last", "-w", "--time-format", "iso", "-n", "800").splitlines():
        m = re.match(r"^(\S+)\s+(\S+)\s+(?:(\S+)\s+)?(\d{4}-\d\d-\d\dT\S+)\s+-\s+(still logged in|\S+)", r)
        if not m:
            continue
        inizio = datetime.strptime(m.group(4), "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc).isoformat(timespec="seconds")
        fine = m.group(5)
        if fine != "still logged in":
            try:
                fine = datetime.strptime(fine, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc).isoformat(timespec="seconds")
            except ValueError:
                pass
        accessi_per.setdefault(m.group(1), []).append({"terminale": m.group(2), "da": m.group(3) or "locale",
                                                       "inizio_utc": inizio, "fine_utc": fine})
    gruppi_speciali = {}
    for g in ("sudo", "docker", "adm", "systemd-journal"):
        try:
            for membro in grp.getgrnam(g).gr_mem:
                gruppi_speciali.setdefault(membro, []).append(g)
        except KeyError:
            pass
    out = []
    for u in utenti_veri():
        miei = [p for p in procs.values() if p["uid"] == u.pw_uid]
        cs = [x for x in sessioni if x["utente"] == u.pw_name] if isinstance(sessioni, list) else []
        out.append({
            "utente": u.pw_name, "uid": u.pw_uid, "home": u.pw_dir, "shell": u.pw_shell,
            "puo_entrare": not u.pw_shell.endswith(("nologin", "false")),
            "gruppi_potenti": (["root"] if u.pw_uid == 0 else []) + gruppi_speciali.get(u.pw_name, []),
            "dentro_adesso": [d for d in acc.get("dentro_adesso", []) if d["utente"] == u.pw_name],
            "ultimi_accessi": accessi_per.get(u.pw_name, [])[:8],
            "processi": len(miei), "ram_mb": sum(p["rss"] for p in miei) // 2**20,
            "processi_piu_pesanti": [{"nome": p["nome"], "pid": p["pid"], "ram_mb": p["rss"] // 2**20}
                                     for p in sorted(miei, key=lambda p: p["rss"], reverse=True)[:3]],
            "claude_lanciati": sum(1 for p in miei if p["nome"] == "claude"),
            "claude_sessioni_14g": len(cs), "claude_ultimo_uso_utc": cs[0]["ultimo_utc"] if cs else None,
            "claude_ultimo_progetto": cs[0]["progetto"] if cs else None,
            "memorie_file": mem_conteggi.get(u.pw_name, 0),
        })
    return out


def _coda(percorso, byte=1_500_000):
    """Ultime righe di un file grande senza leggerlo tutto."""
    with open(percorso, "rb") as f:
        f.seek(0, 2)
        n = f.tell()
        f.seek(max(0, n - byte))
        righe = f.read().decode(errors="replace").splitlines()
    return righe[1:] if n > byte else righe


def claude_sessioni(giorni=14):
    """Ultimi accessi a Claude Code da terminale: ~/.claude/history.jsonl (ogni messaggio scritto, con progetto e
    sessione), raggruppati per sessione."""
    limite = (time.time() - giorni * 86400) * 1000
    sess = {}
    for chi, c in _case_claude():
        p = os.path.join(c, "history.jsonl")
        if not os.path.exists(p):
            continue
        for r in _coda(p):
            try:
                j = json.loads(r)
            except ValueError:
                continue
            ts = j.get("timestamp") or 0
            if ts < limite:
                continue
            k = (chi, j.get("sessionId") or j.get("project", "?"))
            v = sess.setdefault(k, {"utente": chi, "progetto": j.get("project", ""), "sessione": str(k[1])[:8],
                                    "primo_ms": ts, "ultimo_ms": ts, "messaggi": 0, "ultimo_messaggio": ""})
            v["primo_ms"] = min(v["primo_ms"], ts)
            if ts >= v["ultimo_ms"]:
                v["ultimo_ms"] = ts
                v["ultimo_messaggio"] = (j.get("display") or "")[:200]
            v["messaggi"] += 1
    out = []
    for v in sess.values():
        v["inizio_utc"] = datetime.fromtimestamp(v.pop("primo_ms") / 1000, timezone.utc).isoformat(timespec="seconds")
        v["ultimo_utc"] = datetime.fromtimestamp(v.pop("ultimo_ms") / 1000, timezone.utc).isoformat(timespec="seconds")
        out.append(v)
    return sorted(out, key=lambda x: x["ultimo_utc"], reverse=True)[:60]


def memorie():
    """Memorie di Claude Code: ~/.claude/projects/<progetto>/memory/ (MEMORY.md = indice, un file per ricordo)
    e le istruzioni globali ~/.claude/CLAUDE.md. Va in un file a parte (dati/memorie.json)."""
    out = []
    for chi, c in _case_claude():
        g = os.path.join(c, "CLAUDE.md")
        if os.path.isfile(g):
            out.append({"utente": chi, "progetto": "(globale)", "cartella": c, "aggiornato_utc": _mt(g),
                        "file": [{"nome": "CLAUDE.md", "byte": os.path.getsize(g), "aggiornato_utc": _mt(g), "testo": _leggi(g)}]})
        pr = os.path.join(c, "projects")
        if not os.path.isdir(pr):
            continue
        for d in sorted(os.listdir(pr)):
            m = os.path.join(pr, d, "memory")
            if not os.path.isdir(m):
                continue
            files = []
            for f in sorted(os.listdir(m)):
                fp = os.path.join(m, f)
                if os.path.isfile(fp) and f.endswith((".md", ".txt")):
                    files.append({"nome": f, "byte": os.path.getsize(fp), "aggiornato_utc": _mt(fp), "testo": _leggi(fp)})
            if files:
                out.append({"utente": chi, "progetto": d, "cartella": m,
                            "aggiornato_utc": max(x["aggiornato_utc"] for x in files), "file": files})
    return out


def _mt(p):
    return datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).isoformat(timespec="seconds")


def _leggi(p, massimo=60000):
    with open(p, errors="replace") as f:
        return f.read(massimo)


def macchina():
    u = os.uname()
    os_rel = {}
    try:
        with open("/etc/os-release") as f:
            for r in f:
                if "=" in r:
                    k, v = r.rstrip().split("=", 1)
                    os_rel[k] = v.strip('"')
    except OSError:
        pass
    agg = cmd("apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade", t=40)
    return {"nome": u.nodename, "kernel": u.release, "sistema": os_rel.get("PRETTY_NAME", ""),
            "acceso_dal_utc": datetime.fromtimestamp(BTIME, timezone.utc).isoformat(timespec="seconds"),
            "aggiornamenti_in_attesa": len(re.findall(r"^Inst ", agg, re.M)),
            "riavvio_richiesto": os.path.exists("/run/reboot-required")}


def main():
    t0 = time.time()
    procs = tutti_i_processi()
    pezzi = {}
    for nome, f in [("macchina", macchina), ("dischi", dischi), ("gpu", gpu), ("servizi", servizi),
                    ("porte", porte), ("accessi", accessi), ("docker", docker), ("claude_sessioni", claude_sessioni), ("lavori", lavori),
                    ("claude", lambda: claude_lanciati(procs))]:
        try:
            pezzi[nome] = f()
        except Exception as e:
            pezzi[nome] = {"errore": f"{type(e).__name__}: {e}"}
    pezzi["raccolto_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pezzi["raccolto_in_secondi"] = round(time.time() - t0, 2)
    try:
        mem = {"raccolto_utc": pezzi["raccolto_utc"], "memorie": memorie()}
    except Exception as e:
        mem = {"raccolto_utc": pezzi["raccolto_utc"], "errore": f"{type(e).__name__}: {e}", "memorie": []}
    conteggi = {}
    for m in mem["memorie"]:
        conteggi[m["utente"]] = conteggi.get(m["utente"], 0) + len(m["file"])
    try:
        pezzi["utenti"] = utenti(procs, pezzi.get("accessi") if isinstance(pezzi.get("accessi"), dict) else {},
                                 pezzi.get("claude_sessioni"), conteggi)
    except Exception as e:
        pezzi["utenti"] = {"errore": f"{type(e).__name__}: {e}"}
    _scrivi(USCITA, pezzi)
    _scrivi(os.path.join(os.path.dirname(USCITA), "memorie.json"), mem)


def _scrivi(percorso, dati):
    tmp = percorso + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dati, f, ensure_ascii=False)
    os.chmod(tmp, 0o640)
    os.chown(tmp, 0, GRUPPO)
    os.replace(tmp, percorso)


if __name__ == "__main__":
    main()
