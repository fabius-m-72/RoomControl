#!/usr/bin/env bash
# Abilita la modalità kiosk con Chromium su Debian 13 (trixie).
# Crea l'utente dedicato, abilita l'autologin su tty1 e configura un servizio
# systemd utente che avvia Chromium in modalità kiosk con profilo effimero.

set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Esegui questo script come root (o con sudo)." >&2
  exit 1
fi

OS_RELEASE="/etc/os-release"
if [[ -r "$OS_RELEASE" ]]; then
  # shellcheck source=/etc/os-release
  . "$OS_RELEASE"
fi

SUPPORTED_ID="debian"
SUPPORTED_VERSION="13"
ALLOW_UNSUPPORTED=${ALLOW_UNSUPPORTED:-0}

if [[ "${ID:-}" != "$SUPPORTED_ID" || "${VERSION_ID:-}" != "$SUPPORTED_VERSION" ]]; then
  if [[ "$ALLOW_UNSUPPORTED" != "1" ]]; then
    echo "Questo script è pensato per Debian 13 (trixie). Imposta ALLOW_UNSUPPORTED=1 per forzare l'esecuzione." >&2
    exit 1
  else
    echo "[AVVISO] Proseguo su piattaforma non prevista: ${PRETTY_NAME:-sconosciuta}" >&2
  fi
fi

KIOSK_USER=${KIOSK_USER:-kiosk}
KIOSK_URL=${KIOSK_URL:-http://127.0.0.1:8080}
DISPLAY_NUMBER=${DISPLAY_NUMBER:-:0}
GETTY_SERVICE_DIR="/etc/systemd/system/getty@tty1.service.d"
POLICY_DIR="/etc/chromium/policies/managed"
USER_SYSTEMD_DIR="/home/$KIOSK_USER/.config/systemd/user"

export DEBIAN_FRONTEND=noninteractive

echo "[1/6] Installazione pacchetti per l'ambiente grafico e Chromium..."
apt-get update
apt-get install -y \
  chromium \
  xserver-xorg \
  x11-xserver-utils \
  xinit \
  dbus-user-session \
  unclutter \
  policykit-1

if ! id -u "$KIOSK_USER" >/dev/null 2>&1; then
  echo "[2/6] Creo l'utente dedicato $KIOSK_USER senza privilegi sudo..."
  adduser --disabled-password --gecos "" "$KIOSK_USER"
fi

loginctl enable-linger "$KIOSK_USER"

mkdir -p "$GETTY_SERVICE_DIR"
cat > "$GETTY_SERVICE_DIR/autologin.conf" <<'AUTLOGIN'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin KIOSK_USER --noclear %I $TERM
AUTLOGIN
sed -i "s/KIOSK_USER/$KIOSK_USER/g" "$GETTY_SERVICE_DIR/autologin.conf"

echo "[3/6] Configuro la sessione grafica minimale..."
KIOSK_HOME="/home/$KIOSK_USER"
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$KIOSK_HOME"
cat > "$KIOSK_HOME/.bash_profile" <<'BASH_PROFILE'
#!/usr/bin/env bash
if [[ -z "$DISPLAY" && $(tty) == /dev/tty1 ]]; then
  exec startx
fi
BASH_PROFILE

cat > "$KIOSK_HOME/.xinitrc" <<'XINITRC'
#!/usr/bin/env bash
export DISPLAY=DISPLAY_NUMBER
export XAUTHORITY="KIOSK_HOME/.Xauthority"

unclutter -idle 0 &
xmodmap -e "keycode 37 ="  # Disabilita Ctrl_L
xmodmap -e "keycode 64 ="  # Disabilita Alt_L

exec systemctl --user start chromium-kiosk.service
XINITRC
sed -i "s|DISPLAY_NUMBER|$DISPLAY_NUMBER|g" "$KIOSK_HOME/.xinitrc"
sed -i "s|KIOSK_HOME|$KIOSK_HOME|g" "$KIOSK_HOME/.xinitrc"

chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.bash_profile" "$KIOSK_HOME/.xinitrc"
chmod 750 "$KIOSK_HOME/.bash_profile" "$KIOSK_HOME/.xinitrc"

mkdir -p "$USER_SYSTEMD_DIR"
cat > "$USER_SYSTEMD_DIR/chromium-kiosk.service" <<'KIOSK_SERVICE'
[Unit]
Description=Chromium kiosk
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/chromium --kiosk --app=KIOSK_URL \
  --noerrdialogs --disable-session-crashed-bubble --disable-infobars \
  --no-first-run --incognito --disable-sync --disable-translate \
  --overscroll-history-navigation=0 --disable-features=Translate,Autofill,PasswordManagerOnboarding \
  --password-store=basic --check-for-update-interval=31536000
Restart=on-failure
Environment=DISPLAY=DISPLAY_NUMBER XAUTHORITY=KIOSK_HOME/.Xauthority

[Install]
WantedBy=default.target
KIOSK_SERVICE
sed -i "s|KIOSK_URL|$KIOSK_URL|g" "$USER_SYSTEMD_DIR/chromium-kiosk.service"
sed -i "s|DISPLAY_NUMBER|$DISPLAY_NUMBER|g" "$USER_SYSTEMD_DIR/chromium-kiosk.service"
sed -i "s|KIOSK_HOME|$KIOSK_HOME|g" "$USER_SYSTEMD_DIR/chromium-kiosk.service"

chown -R "$KIOSK_USER:$KIOSK_USER" "$USER_SYSTEMD_DIR"
chmod 640 "$USER_SYSTEMD_DIR/chromium-kiosk.service"

mkdir -p "$POLICY_DIR"
cat > "$POLICY_DIR/kiosk.json" <<POLICY
{
  "HomepageLocation": "$KIOSK_URL",
  "HomepageIsNewTabPage": false,
  "RestoreOnStartup": 4,
  "RestoreOnStartupURLs": ["$KIOSK_URL"],
  "PopupBlockingEnabled": true,
  "DefaultSearchProviderEnabled": false,
  "PasswordManagerEnabled": false,
  "DeveloperToolsDisabled": true,
  "ExtensionInstallBlocklist": ["*"]
}
POLICY

systemctl daemon-reload
systemctl restart getty@tty1.service

sudo -u "$KIOSK_USER" systemctl --user daemon-reload
sudo -u "$KIOSK_USER" systemctl --user enable --now chromium-kiosk.service

cat <<EOF
[OK] Modalità kiosk configurata.
- Utente: $KIOSK_USER (autologin su tty1)
- URL: $KIOSK_URL
- Servizio: chromium-kiosk.service (utente)
Riavvia il sistema per applicare completamente la configurazione di autologin.
EOF
