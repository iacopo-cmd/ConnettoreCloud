"""Filtro dei segreti: tutto quello che esce dal connettore passa da qui. Meglio nascondere troppo che troppo poco."""
import re

NASCOSTO = "«nascosto»"

_REGOLE = [
    # token dei bot Telegram 123456789:AA...
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), NASCOSTO),
    # credenziali dentro gli URL: postgresql://utente:password@host
    (re.compile(r"(\w+://)[^/\s:@]+:[^@\s/]+@"), r"\1«utente»:" + NASCOSTO + "@"),
    # Authorization / Bearer
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + NASCOSTO),
    # chiave=valore, chiave: valore (anche in JSON)
    (re.compile(r"""(?ix)
        (["']?[\w.-]*(?:password|passwd|pwd|passphrase|secret|segreto|token|chiave|api[_-]?key|apikey|
                       access[_-]?key|private[_-]?key|client[_-]?secret|cookie|session[_-]?id|auth)[\w.-]*["']?
         \s*[:=]\s*)
        (["']?)[^\s"',;&]{4,}\2"""), r"\1\2" + NASCOSTO + r"\2"),
    # chiavi note
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}"), NASCOSTO),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), NASCOSTO),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"), NASCOSTO),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), NASCOSTO),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), NASCOSTO),   # JWT
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), NASCOSTO),
    # i codici del connettore stesso, se finiscono nei log
    (re.compile(r"/c/[A-Za-z0-9_-]{16,}"), "/c/" + NASCOSTO),
    # stringhe lunghe che sembrano chiavi (hex >= 32, base64 >= 40)
    (re.compile(r"\b[a-fA-F0-9]{32,}\b"), NASCOSTO),
    (re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_-]{40,}={0,2}(?![\w/.-])"), NASCOSTO),
    # dati personali: mail e numeri di telefono
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "«mail»"),
    (re.compile(r"(?<![\w.:-])\+?\d{2}[ ]?3\d{2}[ ]?\d{6,7}\b"), "«telefono»"),
]


def pulisci(testo: str) -> str:
    for r, sost in _REGOLE:
        testo = r.sub(sost, testo)
    return testo


def taglia(testo: str, massimo: int = 20000) -> str:
    if len(testo) <= massimo:
        return testo
    return testo[:massimo] + f"\n… (tagliato: {len(testo) - massimo} caratteri in più)"
