"""OAuth del Connettore cloud: claude.ai (e ogni client MCP che vuole l'OAuth) si collega così.

1. Il client si registra da solo (/register) e manda la persona su /authorize.
2. /authorize porta alla pagina /accedi del connettore: lì si incolla il proprio codice del connettore.
3. Codice giusto → il client riceve un token col profilo di quel codice (root o ristretto).

Il token porta l'impronta del codice («subject»): se il codice viene spento in key/codici.json, anche i token
smettono di valere. Sul disco (dati/oauth.json) ci sono solo impronte dei token, mai i token in chiaro.
"""
import hashlib, html, json, os, secrets, threading, time

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken,
                                      TokenError, construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

AMBITO = "connettore"
DURATA_ACCESSO = 24 * 3600          # 1 giorno
DURATA_RINNOVO = 60 * 24 * 3600     # 60 giorni
DURATA_CODICE_AUTH = 300            # 5 minuti
DURATA_RICHIESTA = 600              # 10 minuti per incollare il codice


def impronta(testo: str) -> str:
    return hashlib.sha256(testo.encode()).hexdigest()


class Fornitore:
    """OAuthAuthorizationServerProvider per la libreria mcp."""

    def __init__(self, percorso_dati: str, percorso_codici: str, base_pubblica: str):
        self.file = percorso_dati
        self.codici = percorso_codici
        self.base = base_pubblica.rstrip("/")
        self._blocco = threading.Lock()
        self.richieste = {}                       # id -> (scadenza, client_id, AuthorizationParams)
        self.interni = {}                         # token per i percorsi /c/<codice>/mcp (solo in memoria)
        self.d = {"client": {}, "codici_auth": {}, "accesso": {}, "rinnovo": {}}
        try:
            with open(self.file) as f:
                self.d.update(json.load(f))
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ disco e codici
    def _salva(self):
        tmp = self.file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.d, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.file)

    def _pulisci(self):
        ora = time.time()
        for k in ("codici_auth", "accesso", "rinnovo"):
            self.d[k] = {h: v for h, v in self.d[k].items() if v.get("scade", 0) > ora}
        self.richieste = {i: r for i, r in self.richieste.items() if r[0] > ora}

    def voce_codice(self, impronta_codice: str):
        """La voce di key/codici.json se il codice esiste ed è acceso (riletta ogni volta: spegnere vale subito)."""
        try:
            with open(self.codici) as f:
                v = json.load(f).get(impronta_codice)
        except (OSError, ValueError):
            return None
        return v if v and not v.get("spento") else None

    def token_interno(self, codice: str) -> str:
        """Per i percorsi /c/<codice>/mcp: un token in memoria legato a quel codice."""
        h = impronta(codice)
        for t, (hh, _) in self.interni.items():
            if hh == h:
                return t
        t = "int_" + secrets.token_urlsafe(32)
        self.interni[t] = (h, time.time())
        return t

    # ------------------------------------------------------------ client
    async def get_client(self, client_id: str):
        c = self.d["client"].get(client_id)
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def register_client(self, client_info: OAuthClientInformationFull):
        with self._blocco:
            self.d["client"][client_info.client_id] = json.loads(client_info.model_dump_json())
            self._salva()

    # ------------------------------------------------------------ autorizzazione
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        with self._blocco:
            self._pulisci()
            rid = secrets.token_urlsafe(24)
            self.richieste[rid] = (time.time() + DURATA_RICHIESTA, client.client_id, params)
        return f"{self.base}/accedi?r={rid}"

    def richiesta(self, rid: str):
        r = self.richieste.get(rid or "")
        return r if r and r[0] > time.time() else None

    def completa(self, rid: str, codice: str):
        """Codice giusto → URL di ritorno al client col codice di autorizzazione; sbagliato → None."""
        r = self.richiesta(rid)
        if not r:
            return None, "Richiesta scaduta: riparti da claude.ai."
        h = impronta(codice.strip())
        voce = self.voce_codice(h)
        if not voce:
            return None, "Codice sbagliato o spento."
        _, client_id, p = r
        codice_auth = secrets.token_urlsafe(32)
        with self._blocco:
            self.richieste.pop(rid, None)
            self.d["codici_auth"][impronta(codice_auth)] = {
                "client_id": client_id, "scopes": p.scopes or [AMBITO], "code_challenge": p.code_challenge,
                "redirect_uri": str(p.redirect_uri), "esplicito": p.redirect_uri_provided_explicitly,
                "resource": p.resource, "subject": h, "scade": time.time() + DURATA_CODICE_AUTH}
            self._salva()
        return construct_redirect_uri(str(p.redirect_uri), code=codice_auth, state=p.state), voce["nome"]

    async def load_authorization_code(self, client, authorization_code: str):
        v = self.d["codici_auth"].get(impronta(authorization_code))
        if not v or v["client_id"] != client.client_id or v["scade"] < time.time():
            return None
        return AuthorizationCode(code=authorization_code, scopes=v["scopes"], expires_at=v["scade"],
                                 client_id=v["client_id"], code_challenge=v["code_challenge"],
                                 redirect_uri=v["redirect_uri"], redirect_uri_provided_explicitly=v["esplicito"],
                                 resource=v.get("resource"), subject=v["subject"])

    def _emetti(self, client_id, scopes, subject, resource) -> OAuthToken:
        accesso, rinnovo = "acc_" + secrets.token_urlsafe(32), "rin_" + secrets.token_urlsafe(32)
        ora = time.time()
        base = {"client_id": client_id, "scopes": scopes, "subject": subject, "resource": resource}
        self.d["accesso"][impronta(accesso)] = {**base, "scade": ora + DURATA_ACCESSO}
        self.d["rinnovo"][impronta(rinnovo)] = {**base, "scade": ora + DURATA_RINNOVO}
        self._salva()
        return OAuthToken(access_token=accesso, token_type="Bearer", expires_in=DURATA_ACCESSO,
                          refresh_token=rinnovo, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        with self._blocco:
            if not self.d["codici_auth"].pop(impronta(authorization_code.code), None):
                raise TokenError("invalid_grant", "codice di autorizzazione già usato")
            if not self.voce_codice(authorization_code.subject):
                raise TokenError("invalid_grant", "codice del connettore spento")
            self._pulisci()
            return self._emetti(client.client_id, authorization_code.scopes, authorization_code.subject,
                                authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token: str):
        v = self.d["rinnovo"].get(impronta(refresh_token))
        if not v or v["client_id"] != client.client_id or v["scade"] < time.time() or not self.voce_codice(v["subject"]):
            return None
        return RefreshToken(token=refresh_token, client_id=v["client_id"], scopes=v["scopes"],
                            expires_at=int(v["scade"]), resource=v.get("resource"), subject=v["subject"])

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        with self._blocco:
            self.d["rinnovo"].pop(impronta(refresh_token.token), None)      # il vecchio non vale più
            return self._emetti(client.client_id, scopes or refresh_token.scopes, refresh_token.subject,
                                refresh_token.resource)

    async def load_access_token(self, token: str):
        if token in self.interni:
            h, _ = self.interni[token]
            if not self.voce_codice(h):
                return None
            return AccessToken(token=token, client_id="codice-diretto", scopes=[AMBITO], subject=h,
                               resource=f"{self.base}/mcp")
        v = self.d["accesso"].get(impronta(token))
        if not v or v["scade"] < time.time() or not self.voce_codice(v["subject"]):
            return None
        return AccessToken(token=token, client_id=v["client_id"], scopes=v["scopes"], expires_at=int(v["scade"]),
                           resource=v.get("resource"), subject=v["subject"])

    async def revoke_token(self, token):
        with self._blocco:
            h = impronta(token.token)
            self.d["accesso"].pop(h, None)
            self.d["rinnovo"].pop(h, None)
            self._salva()


PAGINA = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Connettore cloud</title>
<style>
:root{{--sf:#f6f6f4;--tx:#1c1c1a;--bd:#d6d4ce;--ac:#2f5d50;--er:#a33}}
@media (prefers-color-scheme:dark){{:root{{--sf:#1b1b1a;--tx:#ecebe6;--bd:#3a3a37;--ac:#7fb8a5;--er:#e88}}}}
body{{margin:0;background:var(--sf);color:var(--tx);font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}}
main{{width:min(420px,calc(100% - 32px))}}
h1{{font-size:1.25rem;margin:0 0 .25rem}} p{{margin:.25rem 0 1rem;opacity:.8}}
input{{width:100%;box-sizing:border-box;padding:.7rem;border:1px solid var(--bd);border-radius:8px;background:transparent;color:inherit;font:inherit}}
button{{margin-top:.75rem;width:100%;padding:.7rem;border:0;border-radius:8px;background:var(--ac);color:var(--sf);font:inherit;font-weight:600;cursor:pointer}}
.er{{color:var(--er)}}
</style></head><body><main>
<h1>Connettore cloud · {macchina}</h1>
<p>{testo}</p>
{errore}
{modulo}
</main></body></html>"""


def pagina(macchina: str, testo: str, errore: str = "", rid: str = "") -> str:
    modulo = (f'<form method="post" action="accedi" autocomplete="off">'
              f'<input type="hidden" name="r" value="{html.escape(rid)}">'
              f'<input name="codice" type="password" placeholder="Il tuo codice del connettore" required autofocus>'
              f'<button type="submit">Collega</button></form>') if rid else ""
    return PAGINA.format(macchina=html.escape(macchina), testo=html.escape(testo),
                         errore=f'<p class="er">{html.escape(errore)}</p>' if errore else "", modulo=modulo)
