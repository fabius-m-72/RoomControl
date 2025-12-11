#!/usr/bin/env bash
# Script di deploy per Raspberry Pi 5 con Raspberry Pi OS (Bookworm)
# Installa i pacchetti di sistema, prepara l'ambiente Python e abilita i servizi.
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Esegui questo script come root (o con sudo)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/roomctl"
SYSTEM_USER="roomctl"
PYTHON_BIN="python3"

export DEBIAN_FRONTEND=noninteractive

echo "[1/6] Installazione pacchetti di sistema..."
apt-get update
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  git \
  rsync \
  curl

if ! id -u "$SYSTEM_USER" >/dev/null 2>&1; then
  echo "[2/6] Creo l'utente di servizio $SYSTEM_USER..."
  useradd --system --create-home --shell /usr/sbin/nologin "$SYSTEM_USER"
fi

echo "[3/6] Sincronizzo i sorgenti in $APP_DIR..."
install -d "$APP_DIR"
rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "config/*.yaml" \
  "${SCRIPT_DIR}/" "$APP_DIR/"
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$APP_DIR"

VENV_DIR="$APP_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[4/6] Creo l'ambiente virtuale Python..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install \
  fastapi \
  "uvicorn[standard]" \
  httpx \
  PyYAML \
  python-multipart \
  jinja2

copy_if_absent() {
  local src="$1" dst="$2"
  if [[ -f "$dst" ]]; then
    echo "  - Config già presente: $(basename "$dst")"
  else
    install -D -m 640 "$src" "$dst"
    echo "  - Copiato $(basename "$dst")"
  fi
}

copy_defaults() {
  echo "[5/6] Copio le configurazioni di default (senza sovrascrivere le esistenti)..."
  copy_if_absent "$SCRIPT_DIR/config/config.yaml" "$APP_DIR/config/config.yaml"
  copy_if_absent "$SCRIPT_DIR/config/devices.yaml" "$APP_DIR/config/devices.yaml"
  copy_if_absent "$SCRIPT_DIR/config/ui.yaml" "$APP_DIR/config/ui.yaml"
  copy_if_absent "$SCRIPT_DIR/config/power_schedule.yaml" "$APP_DIR/config/power_schedule.yaml"
}

copy_defaults
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$APP_DIR/config"

install_systemd_unit() {
  local unit_src="$1" unit_dst="$2"
  install -m 644 "$unit_src" "$unit_dst"
  echo "  - Installato $(basename "$unit_dst")"
}

echo "[6/6] Configuro i servizi systemd..."
install_systemd_unit "$APP_DIR/config/roomctl.service" /etc/systemd/system/roomctl.service
systemctl daemon-reload
systemctl enable --now roomctl.service

# Installa e abilita anche il power scheduler
bash "$APP_DIR/config/install_power_scheduler.sh"

echo "Deployment completato. Servizi attivi: roomctl.service e roomctl-power-scheduler.timer"
