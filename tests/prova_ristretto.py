"""Prova di un codice ristretto: python -m tests.prova_ristretto
CONNETTORE_URL = indirizzo col codice ristretto; CONSENTITO = un container che può vedere;
VIETATO = un container che NON deve vedere (deve esistere sulla macchina)."""
import asyncio, os, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL, SI, NO = os.environ["CONNETTORE_URL"], os.environ["CONSENTITO"], os.environ["VIETATO"]
ok = ko = 0


def esito(nome, cond, info=""):
    global ok, ko
    ok, ko = (ok + 1, ko) if cond else (ok, ko + 1)
    print(("OK  " if cond else "NO  ") + nome + (f" — {info[:300]}" if info and not cond else ""))


async def main():
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def t(nome, arg=None):
                res = await s.call_tool(nome, arg or {})
                return "".join(c.text for c in res.content if hasattr(c, "text"))

            x = await t("riassunto")
            esito("riassunto ristretto", "vista ristretta" in x and SI in x, x)
            esito("riassunto senza il vietato", NO not in x, x)
            esito("riassunto senza utenti/SSH/Claude", not any(k in x for k in ("## Utenti", "## Chi è dentro", "## Claude", "## Lavori", "## Porte")), x)
            x = await t("container")
            esito("container: solo i suoi", SI in x and NO not in x, x)
            esito("container: niente montaggi", "montaggi" not in x, x)
            x = await t("container", {"nome": NO})
            esito("container vietato → nessuno", "Nessun container" in x, x)
            x = await t("container", {"nome": SI})
            esito("container consentito → dettaglio", SI in x and "Nessun" not in x, x)
            x = await t("log", {"dove": SI, "righe": 3})
            esito("log consentito", "non è tra" not in x and not x.startswith("Errore"), x)
            x = await t("log", {"dove": NO, "righe": 3})
            esito("log vietato", "non è tra" in x, x)
            x = await t("log", {"dove": "ssh", "righe": 3})
            esito("log di sistema vietato", "non è tra" in x, x)
            x = await t("risorse")
            esito("risorse (salute macchina)", '"ram_gb"' in x, x)
            for nome in ("utenti", "accessi", "claude_e_lavori", "claude_sessioni", "memorie", "processi", "porte", "servizi", "llm"):
                x = await t(nome)
                esito(f"{nome} rifiutato", "non può usare" in x, x)


asyncio.run(main())
print(f"\n{ok} ok, {ko} no")
sys.exit(1 if ko else 0)
