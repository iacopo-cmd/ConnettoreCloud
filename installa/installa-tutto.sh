#!/bin/sh
# Connettore cloud – installazione completa su una macchina nuova, in un colpo. Da lanciare come root:
#   git clone <repo> /opt/ConnettoreCloud && sh /opt/ConnettoreCloud/installa/installa-tutto.sh [nome-codice]
# Si può rilanciare quante volte si vuole: non rifà quello che c'è già (il .env e i codici restano).
#
# Cosa fa:
#   1. controlla root, docker, docker compose, python3
#   2. crea il .env dal modello (nome macchina = nome host, SOLO 127.0.0.1: niente porte pubbliche)
#   3. key/ e dati/ coi permessi giusti
#   4. timer del raccoglitore (di base ogni 5 minuti al secondo 15)
#   5. un codice root (se non ce n'è uno acceso), in chiaro SOLO in key/codici-in-chiaro.txt
#   6. costruisce e accende il container
#   7. prova: il connettore risponde e il codice funziona
# L'uscita pubblica (ASCOLTA_HOST=0.0.0.0, porta, certificato in key/tls, fail2ban) NON la fa da solo:
# si decide a mano nel .env e poi si rilancia questo script.
set -e
CARTELLA=$(cd "$(dirname "$0")/.." && pwd)
cd "$CARTELLA"
NOME_CODICE=${1:-admin}

passo() { printf '\n== %s\n' "$*"; }
muori() { printf 'ERRORE: %s\n' "$*" >&2; exit 1; }

passo "1. controlli"
[ "$(id -u)" = 0 ] || muori "va lanciato come root"
command -v docker >/dev/null || muori "manca docker"
docker compose version >/dev/null 2>&1 || muori "manca docker compose"
command -v python3 >/dev/null || muori "manca python3"
command -v systemctl >/dev/null || muori "manca systemd"
echo "ok: $(hostname), $(docker --version | cut -d, -f1)"

passo "2. .env"
if [ ! -f .env ]; then
    cp .env.esempio .env
    GJ=$(getent group systemd-journal | cut -d: -f3)
    sed -i "s|^GRUPPO_JOURNAL=.*|GRUPPO_JOURNAL=${GJ:-999}|" .env
    echo "creato .env (solo 127.0.0.1; gruppo journal ${GJ:-999})"
else
    echo ".env già presente: non lo tocco"
fi
set -a; . ./.env; set +a
UIDC=${CONNETTORE_UID:-10110}

passo "3. cartelle"
install -d -m 700 key
install -d -o "$UIDC" -g "$UIDC" -m 750 dati
[ -f key/codici.json ] || { echo '{}' > key/codici.json; chown 0:"$UIDC" key/codici.json; chmod 640 key/codici.json; }
[ -d key/tls ] || install -d -m 755 key/tls
echo "key/ (700) e dati/ (utente $UIDC)"

passo "4. raccoglitore"
sh installa/installa-raccoglitore.sh

passo "5. codice"
if python3 -c "import json,sys; d=json.load(open('key/codici.json')); sys.exit(0 if any(v.get('profilo')=='root' and not v.get('spento') for v in d.values()) else 1)"; then
    echo "c'è già un codice root acceso: non ne creo un altro"
else
    CONNETTORE_UID=$UIDC python3 installa/codice-nuovo.py "$NOME_CODICE" root
fi

passo "6. container"
docker compose up -d --build
sleep 6
docker ps --filter name=connettore-cloud --format '{{.Names}}: {{.Status}}'

passo "7. prova"
SCHEMA=http; DOVE=127.0.0.1
if [ -n "$TLS_CERT" ]; then   # col certificato si prova col suo nome (il primo di NOMI_HOST che non è locale)
    SCHEMA=https
    DOVE=$(echo "$NOMI_HOST" | tr ',' '\n' | grep -vE '^(127\.|localhost|::1)' | head -1)
    [ -n "$DOVE" ] || DOVE=127.0.0.1
fi
CODICE=$(grep -v '^SPENTO' key/codici-in-chiaro.txt | tail -1 | cut -f3)
URL="$SCHEMA://$DOVE:${ASCOLTA_PORTA:-63200}/c/$CODICE/mcp"
docker run --rm --network host --user 0 -e CONNETTORE_URL="$URL" -v "$CARTELLA/tests:/srv/tests:ro" \
    --entrypoint python connettore-cloud:attuale -m tests.prova_mcp 2>&1 | grep -E '^NO|ok,' || true
echo
echo "Fatto. Indirizzo: $SCHEMA://$DOVE:${ASCOLTA_PORTA:-63200}/c/<codice>/mcp"
echo "Il codice sta in $CARTELLA/key/codici-in-chiaro.txt (non viene stampato)."
echo "Per claude.ai serve l'uscita pubblica: vedi LEGGIMI.md, «Uscita pubblica»."
