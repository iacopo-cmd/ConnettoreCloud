"""Prova dal vivo: python -m tests.prova_mcp  (URL in CONNETTORE_URL = <base>/c/<codice>/mcp)."""
import asyncio, os, sys, urllib.request, urllib.error
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ["CONNETTORE_URL"]
ok = ko = 0


def esito(nome, cond, info=""):
    global ok, ko
    ok, ko = (ok + 1, ko) if cond else (ok, ko + 1)
    print(("OK  " if cond else "NO  ") + nome + (f" — {info}" if info and not cond else ""))


async def main():
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            esito("initialize", init.serverInfo.name == "Connettore cloud")
            t = await s.list_tools()
            nomi = {x.name for x in t.tools}
            esito("13 strumenti", len(nomi) == 13, str(nomi))
            prove = [("riassunto", {}, ["## Salute", "## Container", "## Chi è dentro", "## Claude Code", "## Lavori", "## Avvisi"]),
                     ("utenti", {}, ['"ultimi_accessi"', '"root"']),
                     ("utenti", {"nome": "root"}, ['"gruppi_potenti"']),
                     ("utenti", {"nome": "nessuno-cosi"}, ["Nessun utente"]),
                     ("claude_sessioni", {}, []),
                     ("claude_sessioni", {"quante": 2}, []),
                     ("memorie", {}, []),
                     ("memorie", {"progetto": "nessun-progetto-cosi"}, ["Nessun progetto"]),
                     ("container", {}, []),
                     ("container", {"nome": "connettore-cloud"}, ['"sola_lettura": true']),
                     ("container", {"nome": "nessun-container-cosi"}, ["Nessun container"]),
                     ("log", {"dove": "connettore-cloud", "righe": 5}, []),
                     ("log", {"dove": "x; rm -rf /", "righe": 3}, ["Nome non valido"]),
                     ("log", {"dove": "connettore-cloud", "righe": 99999}, []),
                     ("servizi", {}, ['"in_esecuzione"', '"timer"']),
                     ("processi", {}, ['"cpu_pct"']),
                     ("processi", {"ordina": "ram", "quanti": 3}, ['"ram_mb"']),
                     ("risorse", {}, ['"ram_gb"', '"dischi"']),
                     ("accessi", {}, ['"dentro_adesso"']),
                     ("claude_e_lavori", {}, ['"claude"', '"lavori"']),
                     ("porte", {}, []),
                     ("porte", {"solo_esterne": False}, ['"solo_locale": true']),
                     ("llm", {}, ['"servizi"'])]
            for nome, arg, attese in prove:
                res = await s.call_tool(nome, arg)
                testo = "".join(c.text for c in res.content if hasattr(c, "text"))
                manca = [a for a in attese if a not in testo]
                esito(f"{nome} {arg}", not res.isError and not manca and not testo.startswith("Errore"),
                      f"mancano {manca}: {testo[:300]}")
                if nome == "log" and arg.get("righe") == 99999:
                    esito("log al massimo 200 righe", testo.count("\n") <= 201, str(testo.count("\n")))
                if nome == "riassunto" and os.environ.get("MOSTRA"):
                    print(testo)


asyncio.run(main())
base = URL.split("/c/")[0]
for nome, url, atteso in [("codice sbagliato", base + "/c/" + "x" * 43 + "/mcp", 404),
                          ("codice corto", base + "/c/abc/mcp", 404),
                          ("senza codice", base + "/mcp", 404),
                          ("radice", base + "/", 404)]:
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=b"{}", headers={"content-type": "application/json"}), timeout=5)
        c = 200
    except urllib.error.HTTPError as e:
        c = e.code
    esito(f"{nome} → {atteso}", c == atteso, str(c))
codice = URL.split("/c/")[1].split("/")[0]
for scoperta in ["/.well-known/oauth-protected-resource", f"/.well-known/oauth-protected-resource/c/{codice}/mcp",
                 "/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"]:
    try:
        urllib.request.urlopen(base + scoperta, timeout=5); c = 200
    except urllib.error.HTTPError as e:
        c = e.code
    esito(f"scoperta OAuth {scoperta.split('/c/')[0]} → 404", c == 404, str(c))
req = urllib.request.Request(URL, data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                             headers={"content-type": "application/json", "accept": "application/json, text/event-stream", "host": "cattivo.example"})
try:
    urllib.request.urlopen(req, timeout=5); c = 200
except urllib.error.HTTPError as e:
    c = e.code
esito("Host estraneo rifiutato (DNS rebinding)", c in (403, 421), str(c))
print(f"\n{ok} ok, {ko} no")
sys.exit(1 if ko else 0)
