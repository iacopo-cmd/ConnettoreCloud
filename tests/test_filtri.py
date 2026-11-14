from app.filtri import pulisci, taglia

CASI = [
    ("token bot 1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1 qui", "AAHdq"),
    ("postgresql://app:segretissima@postgres:5432/app", "segretissima"),
    ("Authorization: Bearer abcdefghijklmnop123456", "abcdefghijklmnop123456"),
    ("PASSWORD=ciao12345", "ciao12345"),
    ('{"api_key": "qwertyuiop123"}', "qwertyuiop123"),
    ("TELEGRAM_WEBHOOK_SECRET=zzzzzzzz9", "zzzzzzzz9"),
    ("chiave: valoresegreto1", "valoresegreto1"),
    ("sk-ant-api03-abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvwx"),
    ("ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghijk"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "dozjgN"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----", "abc\n"),
    ("GET /c/Abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG/mcp", "Abcdefghijklmnop"),
    ("hash 0123456789abcdef0123456789abcdef01", "0123456789abcdef0123"),
    ("scrivi a mario.rossi@example.com", "mario.rossi"),
    ("chiama +39 333 1234567", "1234567"),
]


def test_segreti_nascosti():
    for testo, segreto in CASI:
        assert segreto not in pulisci(testo), (testo, pulisci(testo))


def test_testo_normale_intatto():
    for t in ["orariocrew1-web: running, 2h 3m, salute healthy", "Disco /: 138.3/453.7 GB (33%)",
              "2026-11-14T17:49:23+00:00 entrato mario", "/srv/progetto/app/main.py:12",
              "tcp 0.0.0.0:8443 python3"]:
        assert pulisci(t) == t, (t, pulisci(t))


def test_taglia():
    assert len(taglia("x" * 30000)) < 20100 and "tagliato" in taglia("x" * 30000)
