"""Prova dal vivo del giro OAuth (come claude.ai): python -m tests.prova_oauth
BASE = indirizzo pubblico del connettore (es. https://dominio:porta); CODICE = un codice root acceso;
CODICE_RISTRETTO (facoltativo) = un codice ristretto, con CONSENTITO e VIETATO come in prova_ristretto."""
import asyncio, base64, hashlib, json, os, re, secrets, sys, urllib.error, urllib.parse, urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = os.environ["BASE"].rstrip("/")
RITORNO = "https://claude.ai/api/mcp/auth_callback"
ok = ko = 0


def esito(nome, cond, info=""):
    global ok, ko
    ok, ko = (ok + 1, ko) if cond else (ok, ko + 1)
    print(("OK  " if cond else "NO  ") + nome + (f" — {str(info)[:300]}" if info and not cond else ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


apri = urllib.request.build_opener(_NoRedirect()).open


def chiama(metodo, url, dati=None, intestazioni=None, json_body=None):
    h = dict(intestazioni or {})
    corpo = None
    if json_body is not None:
        corpo, h["content-type"] = json.dumps(json_body).encode(), "application/json"
    elif dati is not None:
        corpo, h["content-type"] = urllib.parse.urlencode(dati).encode(), "application/x-www-form-urlencoded"
    try:
        r = apri(urllib.request.Request(url, data=corpo, headers=h, method=metodo), timeout=10)
        return r.status, dict(r.headers), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")


def giro(codice, nome):
    """Registrazione → authorize → accedi → token. Torna (client_id, token)."""
    s, _, b = chiama("POST", BASE + "/register", json_body={
        "redirect_uris": [RITORNO], "client_name": f"prova {nome}", "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    esito(f"[{nome}] /register", s in (200, 201), f"{s} {b}")
    cid = json.loads(b)["client_id"]
    verificatore = secrets.token_urlsafe(48)
    sfida = base64.urlsafe_b64encode(hashlib.sha256(verificatore.encode()).digest()).rstrip(b"=").decode()
    stato = secrets.token_urlsafe(8)
    q = urllib.parse.urlencode({"response_type": "code", "client_id": cid, "redirect_uri": RITORNO,
                                "code_challenge": sfida, "code_challenge_method": "S256", "state": stato,
                                "scope": "connettore", "resource": BASE + "/mcp"})
    s, h, b = chiama("GET", BASE + "/authorize?" + q)
    dove = h.get("Location") or h.get("location") or ""
    esito(f"[{nome}] /authorize → /accedi", s in (302, 307) and "/accedi?r=" in dove, f"{s} {dove} {b[:200]}")
    rid = urllib.parse.parse_qs(urllib.parse.urlparse(dove).query)["r"][0]
    s, _, b = chiama("GET", dove)
    esito(f"[{nome}] pagina /accedi", s == 200 and 'name="codice"' in b, s)
    s, h, b = chiama("POST", BASE + "/accedi", dati={"r": rid, "codice": "sbagliato-" + "x" * 30})
    esito(f"[{nome}] codice sbagliato → 403", s == 403 and "sbagliato" in b, s)
    s, h, b = chiama("POST", BASE + "/accedi", dati={"r": rid, "codice": codice})
    dove = h.get("Location") or h.get("location") or ""
    qq = urllib.parse.parse_qs(urllib.parse.urlparse(dove).query)
    esito(f"[{nome}] codice giusto → ritorno a claude.ai", s == 302 and dove.startswith(RITORNO) and qq.get("state") == [stato], f"{s} {dove[:80]}")
    s, _, b = chiama("POST", BASE + "/token", dati={"grant_type": "authorization_code", "code": qq["code"][0],
                     "redirect_uri": RITORNO, "client_id": cid, "code_verifier": verificatore})
    esito(f"[{nome}] /token", s == 200 and "access_token" in b, f"{s} {b}")
    t = json.loads(b)
    s, _, b = chiama("POST", BASE + "/token", dati={"grant_type": "authorization_code", "code": qq["code"][0],
                     "redirect_uri": RITORNO, "client_id": cid, "code_verifier": verificatore})
    esito(f"[{nome}] codice di autorizzazione usato due volte → rifiutato", s == 400, s)
    s, _, b = chiama("POST", BASE + "/token", dati={"grant_type": "refresh_token", "refresh_token": t["refresh_token"],
                     "client_id": cid})
    esito(f"[{nome}] rinnovo del token", s == 200 and "access_token" in b, f"{s} {b}")
    nuovo = json.loads(b)
    s, _, _ = chiama("POST", BASE + "/token", dati={"grant_type": "refresh_token", "refresh_token": t["refresh_token"],
                     "client_id": cid})
    esito(f"[{nome}] vecchio token di rinnovo non vale più", s == 400, s)
    return cid, nuovo["access_token"]


async def strumenti(token, attese_in_riassunto, rifiutati=()):
    async with streamablehttp_client(BASE + "/mcp", headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t = await s.list_tools()
            res = await s.call_tool("riassunto", {})
            testo = "".join(c.text for c in res.content if hasattr(c, "text"))
            out = {"strumenti": len(t.tools), "riassunto": testo}
            for n in rifiutati:
                rr = await s.call_tool(n, {})
                out[n] = "".join(c.text for c in rr.content if hasattr(c, "text"))
            return out


if os.environ.get("SOLO_CONTROLLO"):
    tok = open(os.environ["TOKEN_IN"]).read().strip()
    s, _, _ = chiama("POST", BASE + "/mcp", json_body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                     intestazioni={"accept": "application/json, text/event-stream", "Authorization": f"Bearer {tok}"})
    esito("codice spento → il suo token non vale più (401)", s == 401, s)
    print(f"\n{ok} ok, {ko} no")
    sys.exit(1 if ko else 0)

# ---------------------------------------------------------------- scoperta
s, h, _ = chiama("POST", BASE + "/mcp", json_body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                 intestazioni={"accept": "application/json, text/event-stream"})
www = h.get("WWW-Authenticate") or h.get("www-authenticate") or ""
esito("/mcp senza token → 401 con resource_metadata", s == 401 and "resource_metadata" in www, f"{s} {www}")
meta_url = re.search(r'resource_metadata="([^"]+)"', www).group(1)
s, _, b = chiama("GET", meta_url)
esito("metadati della risorsa", s == 200 and "authorization_servers" in b, f"{s} {b}")
s, _, b = chiama("GET", BASE + "/.well-known/oauth-authorization-server")
m = json.loads(b) if s == 200 else {}
esito("metadati del server OAuth", s == 200 and m.get("registration_endpoint", "").endswith("/register")
      and "S256" in m.get("code_challenge_methods_supported", []), f"{s} {b}")
s, _, _ = chiama("POST", BASE + "/mcp", json_body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                 intestazioni={"accept": "application/json, text/event-stream", "Authorization": "Bearer finto"})
esito("token finto → 401", s == 401, s)

# ---------------------------------------------------------------- root
_, tok = giro(os.environ["CODICE"], "root")
x = asyncio.run(strumenti(tok, []))
esito("[root] strumenti col token OAuth", x["strumenti"] == 13 and "## Salute" in x["riassunto"] and "## Utenti" in x["riassunto"],
      x["riassunto"][:300])

# ---------------------------------------------------------------- ristretto
if os.environ.get("CODICE_RISTRETTO"):
    _, tok = giro(os.environ["CODICE_RISTRETTO"], "ristretto")
    x = asyncio.run(strumenti(tok, [], rifiutati=("utenti", "memorie")))
    esito("[ristretto] vista ristretta via OAuth", "vista ristretta" in x["riassunto"] and os.environ["CONSENTITO"] in x["riassunto"]
          and os.environ["VIETATO"] not in x["riassunto"], x["riassunto"][:300])
    esito("[ristretto] utenti e memorie rifiutati", "non può usare" in x["utenti"] and "non può usare" in x["memorie"])

# ---------------------------------------------------------------- codice spento
if os.environ.get("CODICE_DA_SPEGNERE") and os.environ.get("TOKEN_IN"):
    # prima metà: prende un token col codice e lo lascia nel file TOKEN_IN; la seconda metà (dopo lo
    # spegnimento del codice) è la prova con SOLO_CONTROLLO=1
    _, tok = giro(os.environ["CODICE_DA_SPEGNERE"], "da spegnere")
    open(os.environ["TOKEN_IN"], "w").write(tok)

print(f"\n{ok} ok, {ko} no")
sys.exit(1 if ko else 0)
