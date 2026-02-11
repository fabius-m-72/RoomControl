#!/usr/bin/env bash
# Script di deploy per Raspberry Pi 5 con Debian 13.3 (Trixie).
# Azioni eseguite:
# - verifica l'esecuzione come root e imposta l'ambiente non interattivo per APT
# - aggiorna l'indice pacchetti e installa dipendenze di sistema (Python, git, rsync, curl)
# - crea l'utente 'roomctl' se assente e imposta la password 1qaz"WSX
# - imposta la password 3edc$RFV per 'root'
# - sincronizza i sorgenti applicativi in /opt/roomctl ed imposta i permessi
# - crea un virtualenv Python e installa le dipendenze applicative
# - copia le configurazioni di default senza sovrascrivere le esistenti
# - configura il boot per ricarica batteria RTC (overlay rtc-rp1)
# - installa e abilita i servizi systemd (roomctl e power scheduler)
# Troubleshooting:
# - verificare log servizio roomctl: journalctl -b -u roomctl.service
# - verificare log scheduler: journalctl -b -u roomctl-power-scheduler.service
# - verificare errori API: curl -f http://127.0.0.1:8080/api/health (se disponibile)
# - verificare stato RTC: timedatectl status e hwclock -r
# - se roomctl non parte: controllare dipendenze Python in /opt/roomctl/.venv
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Esegui questo script come root (o con sudo)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/roomctl"
SYSTEM_USER="roomctl"
PYTHON_BIN="python3"
BOOT_CONFIG="/boot/firmware/config.txt"

export DEBIAN_FRONTEND=noninteractive

echo "[1/8] Installazione pacchetti di sistema..."
apt-get update
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  git \
  rsync \
  curl

if ! id -u "$SYSTEM_USER" >/dev/null 2>&1; then
  echo "[2/8] Creo l'utente di servizio $SYSTEM_USER..."
  useradd --create-home --shell /bin/bash "$SYSTEM_USER"
fi

echo "[3/8] Imposto le password per $SYSTEM_USER e root..."
printf '%s\n' \
  "${SYSTEM_USER}:1qaz\"WSX" \
  "root:3edc\$RFV" | chpasswd

echo "[4/8] Sincronizzo i sorgenti in $APP_DIR..."
install -d "$APP_DIR"
rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "config/*.yaml" \
  --exclude "config/power_scheduler.py" \
  "${SCRIPT_DIR}/" "$APP_DIR/"
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$APP_DIR"

VENV_DIR="$APP_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[5/8] Creo l'ambiente virtuale Python..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install \
  fastapi \
  "uvicorn[standard]" \
  httpx \
  PyYAML \
  python-multipart \
  requests \
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
  echo "[6/8] Copio le configurazioni di default (senza sovrascrivere le esistenti)..."
  copy_if_absent "$SCRIPT_DIR/config/config.yaml" "$APP_DIR/config/config.yaml"
  copy_if_absent "$SCRIPT_DIR/config/devices.yaml" "$APP_DIR/config/devices.yaml"
  copy_if_absent "$SCRIPT_DIR/config/ui.yaml" "$APP_DIR/config/ui.yaml"
  copy_if_absent "$SCRIPT_DIR/config/power_schedule.yaml" "$APP_DIR/config/power_schedule.yaml"
}

copy_defaults
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$APP_DIR/config"

ensure_dtparam_setting() {
  local param="$1" value="$2"
  if [[ -f "$BOOT_CONFIG" ]]; then
    if grep -qE "^dtparam=.*\\b${param}=" "$BOOT_CONFIG"; then
      sed -i -E "s/^(dtparam=.*)${param}=[^,]+/\\1${param}=${value}/" "$BOOT_CONFIG"
    else
      echo "dtparam=${param}=${value}" >> "$BOOT_CONFIG"
    fi
  fi
}

ensure_boot_line() {
  local line="$1"
  if [[ -f "$BOOT_CONFIG" ]]; then
    if ! grep -Fxq "$line" "$BOOT_CONFIG"; then
      echo "$line" >> "$BOOT_CONFIG"
    fi
  fi
}

echo "[7/8] Configuro ricarica batteria RTC..."
ensure_boot_line "dtoverlay=rtc-rp1"
ensure_dtparam_setting "rtc_bbat_vchg" "3000000"

install_systemd_unit() {
  local unit_src="$1" unit_dst="$2"
  install -m 644 "$unit_src" "$unit_dst"
  echo "  - Installato $(basename "$unit_dst")"
}

echo "[8/8] Configuro i servizi systemd..."
install_systemd_unit "$APP_DIR/config/roomctl.service" /etc/systemd/system/roomctl.service
systemctl daemon-reload
systemctl enable --now roomctl.service

# Installa e abilita anche il power scheduler
bash "$APP_DIR/config/install_power_scheduler.sh"

echo "[POST] Verifica servizi e API..."
systemctl --no-pager --full status roomctl.service || true
systemctl --no-pager --full status roomctl-power-scheduler.service || true
systemctl --no-pager --full status roomctl-power-scheduler.timer || true
curl --fail --silent --show-error "http://127.0.0.1:8080/" >/dev/null || true

echo "Deployment completato. Servizi attivi: roomctl.service e roomctl-power-scheduler.timer"
