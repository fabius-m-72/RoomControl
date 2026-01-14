#!/usr/bin/env bash
# Script di deploy per Raspberry Pi 5 con Debian 13.3 (Trixie).
# Azioni eseguite:
# - verifica l'esecuzione come root e imposta l'ambiente non interattivo per APT
# - aggiorna l'indice pacchetti e installa dipendenze di sistema (Python, git, rsync, curl)
# - crea l'utente di servizio dedicato se assente
# - sincronizza i sorgenti applicativi in /opt/roomctl ed imposta i permessi
# - crea un virtualenv Python e installa le dipendenze applicative
# - copia le configurazioni di default senza sovrascrivere le esistenti
# - configura il boot (schermata nera, rotazione display/touch, ricarica batteria RTC)
# - installa e abilita i servizi systemd (roomctl e power scheduler)
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
CMDLINE_FILE="/boot/firmware/cmdline.txt"

export DEBIAN_FRONTEND=noninteractive

echo "[1/7] Installazione pacchetti di sistema..."
apt-get update
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  git \
  rsync \
  curl

if ! id -u "$SYSTEM_USER" >/dev/null 2>&1; then
  echo "[2/7] Creo l'utente di servizio $SYSTEM_USER..."
  useradd --system --create-home --shell /usr/sbin/nologin "$SYSTEM_USER"
fi

echo "[3/7] Sincronizzo i sorgenti in $APP_DIR..."
install -d "$APP_DIR"
rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "config/*.yaml" \
  "${SCRIPT_DIR}/" "$APP_DIR/"
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$APP_DIR"

VENV_DIR="$APP_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[4/7] Creo l'ambiente virtuale Python..."
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
  echo "[5/7] Copio le configurazioni di default (senza sovrascrivere le esistenti)..."
  copy_if_absent "$SCRIPT_DIR/config/config.yaml" "$APP_DIR/config/config.yaml"
  copy_if_absent "$SCRIPT_DIR/config/devices.yaml" "$APP_DIR/config/devices.yaml"
  copy_if_absent "$SCRIPT_DIR/config/ui.yaml" "$APP_DIR/config/ui.yaml"
  copy_if_absent "$SCRIPT_DIR/config/power_schedule.yaml" "$APP_DIR/config/power_schedule.yaml"
}

copy_defaults
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$APP_DIR/config"

ensure_config_setting() {
  local key="$1" value="$2"
  if [[ -f "$BOOT_CONFIG" ]]; then
    if grep -qE "^${key}=" "$BOOT_CONFIG"; then
      sed -i "s|^${key}=.*|${key}=${value}|" "$BOOT_CONFIG"
    else
      echo "${key}=${value}" >> "$BOOT_CONFIG"
    fi
  fi
}

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

update_cmdline() {
  if [[ ! -f "$CMDLINE_FILE" ]]; then
    return
  fi

  local cmdline
  cmdline="$(tr -d '\n' < "$CMDLINE_FILE")"
  local tokens=($cmdline)
  local updated=()
  for token in "${tokens[@]}"; do
    case "$token" in
      console=tty1|console=serial0,115200)
        continue
        ;;
      *)
        updated+=("$token")
        ;;
    esac
  done

  add_token() {
    local value="$1"
    for existing in "${updated[@]}"; do
      if [[ "$existing" == "$value" ]]; then
        return
      fi
    done
    updated+=("$value")
  }

  add_token "quiet"
  add_token "loglevel=0"
  add_token "logo.nologo"
  add_token "vt.global_cursor_default=0"
  add_token "plymouth.enable=0"
  add_token "fbcon=rotate:1"

  printf '%s\n' "${updated[*]}" > "$CMDLINE_FILE"
}

echo "[6/7] Configuro boot (schermata nera, rotazione, RTC)..."
ensure_config_setting "disable_splash" "1"
ensure_config_setting "display_rotate" "1"
ensure_config_setting "lcd_rotate" "1"
ensure_dtparam_setting "rtc_bbat_vchg" "3000000"
update_cmdline

install_systemd_unit() {
  local unit_src="$1" unit_dst="$2"
  install -m 644 "$unit_src" "$unit_dst"
  echo "  - Installato $(basename "$unit_dst")"
}

echo "[7/7] Configuro i servizi systemd..."
install_systemd_unit "$APP_DIR/config/roomctl.service" /etc/systemd/system/roomctl.service
systemctl daemon-reload
systemctl enable --now roomctl.service

# Installa e abilita anche il power scheduler
bash "$APP_DIR/config/install_power_scheduler.sh"

echo "Deployment completato. Servizi attivi: roomctl.service e roomctl-power-scheduler.timer"
