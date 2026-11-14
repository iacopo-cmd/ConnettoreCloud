#!/bin/sh
# Da lanciare come root sulla macchina: mette il timer del raccoglitore (sola lettura). Ogni quanto: nel .env
# RACCOLTA_OGNI_MINUTI (5) e RACCOLTA_AL_SECONDO (15). Cambiati quelli, si rilancia questo script.
set -e
CARTELLA=$(cd "$(dirname "$0")/.." && pwd)
. "$CARTELLA/.env" 2>/dev/null || true
install -d -o "${CONNETTORE_UID:-10110}" -g "${CONNETTORE_UID:-10110}" -m 750 "$CARTELLA/dati"
install -d -m 700 "$CARTELLA/key"
sed "s|@CARTELLA@|$CARTELLA|g" "$CARTELLA/installa/connettore-raccogli.service" > /etc/systemd/system/connettore-raccogli.service
OGNI=${RACCOLTA_OGNI_MINUTI:-5}; SECONDO=${RACCOLTA_AL_SECONDO:-15}
sed "s|@OGNI@|$OGNI|g; s|@SECONDO@|$SECONDO|g" "$CARTELLA/installa/connettore-raccogli.timer" > /etc/systemd/system/connettore-raccogli.timer
systemctl daemon-reload
systemctl enable --now connettore-raccogli.timer
systemctl start connettore-raccogli.service
echo "raccoglitore acceso ogni $OGNI minuti al secondo $SECONDO: $CARTELLA/dati/stato-host.json"
systemctl list-timers connettore-raccogli.timer --no-pager | head -2
