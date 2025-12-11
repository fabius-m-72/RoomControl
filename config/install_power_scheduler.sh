#!/usr/bin/env bash
# Prima inizializzazione del power scheduler su un Raspberry "gemello".
# Esegue la copia degli script/unit e abilita il timer systemd.
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Esegui questo script come root (o con sudo)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="/opt/roomctl"
CONFIG_DIR="$PREFIX/config"
SERVICE_NAME="roomctl-power-scheduler"

mkdir -p "$CONFIG_DIR"
install -m 755 "$SCRIPT_DIR/power_scheduler.py" "$CONFIG_DIR/power_scheduler.py"
install -m 644 "$SCRIPT_DIR/${SERVICE_NAME}.service" /etc/systemd/system/
install -m 644 "$SCRIPT_DIR/${SERVICE_NAME}.timer" /etc/systemd/system/

# Copia un file di configurazione vuoto se non esiste, così l'interfaccia può salvarlo.
SCHEDULE_FILE="$CONFIG_DIR/power_schedule.yaml"
if [[ ! -e "$SCHEDULE_FILE" ]]; then
  install -m 644 /dev/null "$SCHEDULE_FILE"
fi

systemctl daemon-reload
systemctl enable --now ${SERVICE_NAME}.timer

# Esegue subito lo scheduler per programmare la prossima accensione/spegnimento.
/usr/bin/python3 -u "$CONFIG_DIR/power_scheduler.py" || true

echo "Installazione completata. Puoi verificare con: systemctl status ${SERVICE_NAME}.timer"