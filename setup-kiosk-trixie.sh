#!/usr/bin/env bash
set -euo pipefail

################################################################################
# setup-kiosk-trixie.sh
#
# Raspberry Pi OS Trixie (Debian 13) - Kiosk landscape su Touch Display 2
# - Weston (kiosk-shell) + Chromium kiosk
# - Layout landscape (rotate-90 / rotate-270)
# - Niente scroll touch (1/2 dita), niente inerzia, niente swipe, niente pinch
# - Tap/click OK, scroll solo via scrollbar
# - Boot silenzioso + niente desktop (multi-user) + systemd service su tty1
#
# Uso:
#   1) Salva questo file: setup-kiosk-trixie.sh
#   2) chmod +x setup-kiosk-trixie.sh
#   3) sudo ./setup-kiosk-trixie.sh
#
# Personalizzazione (prima di eseguire):
#   - KIOSK_URL: pagina da aprire
#   - ROTATION: rotate-90 oppure rotate-270
#   - KIOSK_USER: utente che esegue il kiosk (default: SUDO_USER o "pi")
#   - EXT_SCOPE: "domain" (default) oppure "all" per applicare no-scroll a tutti i siti
################################################################################
#
# - - - - -  Note Manutenzione - - - - - - - - - - - - - - - - - - - - - - - - 
# - per uscire dal kiosk usare Ctrl+Alt+F2, fare login come amministratore
# - per fermare temporaneamente il kiosk: systemctl stop kiosk.service
# - per riattivarlo: systemctl restart kiosk.service
# - per disabilitare l'autostart di Chromium: systemctl disable --now kiosk.service
#
# - - - - Disinstallazione / ritorno al desktop - - - - - - - - - - - - - - - -
# sudo systemctl disable --now kiosk.service
# sudo systemctl set-default graphical.target
# sudo systemctl enable --now getty@tty1.service
# sudo reboot
################################################################################
# ====== PARAMETRI MODIFICABILI ======
KIOSK_URL="${KIOSK_URL:-127.0.0.1:8080}"
ROTATION="${ROTATION:-rotate-270}"        # rotate-90 oppure rotate-270
EXT_SCOPE="${EXT_SCOPE:-domain}"          # domain | all
KIOSK_USER="${KIOSK_USER:-${SUDO_USER:-roomctl}}"
# ====================================

BOOT_CMDLINE="/boot/firmware/cmdline.txt"
BOOT_CONFIG="/boot/firmware/config.txt"

SERVICE_NAME="kiosk.service"
KIOSK_DIR="/home/${KIOSK_USER}/kiosk"
EXT_DIR="${KIOSK_DIR}/no-touch-scroll"
WESTON_INI="/home/${KIOSK_USER}/.config/weston.ini"
CHROMIUM_PROFILE="/home/${KIOSK_USER}/.config/chromium-kiosk"

die() { echo "ERRORE: $*" >&2; exit 1; }

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "Esegui come root (usa sudo)."
}

user_exists() {
  id "${KIOSK_USER}" >/dev/null 2>&1
}

backup_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  cp -a "$f" "${f}.bak.${ts}"
  echo "Backup creato: ${f}.bak.${ts}"
}

# Rileva automaticamente l’output DSI connesso (DSI-1 o DSI-2)
detect_dsi_output() {
  local dsi
  dsi="$(for p in /sys/class/drm/card*-DSI-*/status; do
           [[ -f "$p" ]] || continue
           if [[ "$(cat "$p")" == "connected" ]]; then
             basename "$(dirname "$p")" | sed -E 's/^card[0-9]+-//'
             break
           fi
         done)"
  [[ -n "$dsi" ]] || die "Nessun output DSI connesso trovato in /sys/class/drm (controlla cablaggio DSI)."
  echo "$dsi"
}

# Aggiorna cmdline.txt (una sola riga) per boot silenzioso:
# - rimuove console=ttyX
# - assicura console seriale
# - aggiunge quiet/splash/loglevel ecc.
update_cmdline_quiet() {
  [[ -f "$BOOT_CMDLINE" ]] || die "File non trovato: $BOOT_CMDLINE"
  backup_file "$BOOT_CMDLINE"

  local line
  line="$(tr -d '\n' < "$BOOT_CMDLINE")"

  # Rimuovi eventuali console=tty*
  line="$(echo "$line" | sed -E 's/\s*console=tty[0-9]+\s*/ /g')"
  # Evita duplicati di spazi
  line="$(echo "$line" | tr -s ' ')"

  # Assicura console seriale (se manca)
  if ! echo "$line" | grep -qE '(^| )console=serial0,115200( |$)'; then
    line="console=serial0,115200 ${line}"
  fi

  # Parametri "quiet boot" (aggiungili solo se mancanti)
  for p in \
    "quiet" \
    "splash" \
    "plymouth.ignore-serial-consoles" \
    "loglevel=0" \
    "logo.nologo" \
    "vt.global_cursor_default=0" \
    "systemd.show_status=false" \
    "rd.systemd.show_status=false"
  do
    if ! echo "$line" | grep -qE "(^| )${p}( |$)"; then
      line="${line} ${p}"
    fi
  done

  # Scrivi come una singola riga + newline finale
  echo "$line" > "$BOOT_CMDLINE"
  echo "Aggiornato: $BOOT_CMDLINE (boot silenzioso; nessuna console su display)"
}

# Assicura in config.txt:
# - vc4-kms-v3d
# - disable_splash=1
update_config_txt() {
  [[ -f "$BOOT_CONFIG" ]] || die "File non trovato: $BOOT_CONFIG"
  backup_file "$BOOT_CONFIG"

  # Aggiungi disable_splash=1 se manca
  if ! grep -qE '^\s*disable_splash=1\s*$' "$BOOT_CONFIG"; then
    echo -e "\n# Kiosk: riduci splash\ndisable_splash=1" >> "$BOOT_CONFIG"
  fi

  # Assicura dtoverlay=vc4-kms-v3d (se manca, aggiungilo)
  if ! grep -qE '^\s*dtoverlay=vc4-kms-v3d(\s|$)' "$BOOT_CONFIG"; then
    echo -e "\n# Kiosk: DRM VC4 V3D\ndtoverlay=vc4-kms-v3d" >> "$BOOT_CONFIG"
  fi

  echo "Aggiornato: $BOOT_CONFIG (vc4-kms-v3d + disable_splash=1)"
}

install_packages() {
  echo "Installo pacchetti (weston, chromium, curl)..."
  apt-get update
  apt-get install -y weston chromium curl
}

write_weston_ini() {
  local dsi_output="$1"
  mkdir -p "/home/${KIOSK_USER}/.config"
  cat > "$WESTON_INI" <<EOF
# Weston kiosk config
# - kiosk-shell: nessun desktop, una sola app fullscreen
# - idle-time=0: niente blanking/sleep
# - output: rotazione landscape su ${dsi_output}
[core]
backend=drm-backend.so
shell=kiosk-shell.so
idle-time=0

[output]
name=${dsi_output}
mode=preferred
transform=${ROTATION}
EOF
  chown -R "${KIOSK_USER}:${KIOSK_USER}" "/home/${KIOSK_USER}/.config"
  echo "Creato: $WESTON_INI (output=${dsi_output}, transform=${ROTATION})"
}

# Crea estensione MV3:
# - inietta CSS touch-action:none su html/body
#   => blocca scroll/pan/inerzia/swipe/pinch del browser
#   => tap/click resta funzionante
write_no_touch_scroll_extension() {
  mkdir -p "$EXT_DIR"

  # Deriva host dall'URL (per scope "domain")
  local host stripped_www www_host
  host="$(echo "$KIOSK_URL" | sed -E 's#^[a-zA-Z]+://##; s#/.*##')"
  stripped_www="${host#www.}"
  www_host="www.${stripped_www}"

  local matches_json
  if [[ "$EXT_SCOPE" == "all" ]]; then
    matches_json='["<all_urls>"]'
  else
    # includi sia con che senza www, e http/https
    matches_json="$(cat <<M
[
  "https://${host}/*",
  "http://${host}/*",
  "https://${stripped_www}/*",
  "http://${stripped_www}/*",
  "https://${www_host}/*",
  "http://${www_host}/*"
]
M
)"
  fi

  cat > "${EXT_DIR}/manifest.json" <<EOF
{
  "manifest_version": 3,
  "name": "No Touch Scroll",
  "version": "1.0",
  "description": "Disabilita scroll/inerzia/swipe/pinch via touch; lascia tap/click e scrollbar.",
  "content_scripts": [
    {
      "matches": ${matches_json},
      "js": ["content.js"],
      "run_at": "document_start"
    }
  ]
}
EOF

  cat > "${EXT_DIR}/content.js" <<'EOF'
/*
  Blocca le gesture touch gestite dal browser (scroll/pan/inerzia/swipe/pinch)
  mantenendo tap/click.

  touch-action:none dice al browser: "non gestire gesture di panning/zoom".
  Non facciamo preventDefault su touchstart/touchend per non rompere i click.
*/
(function () {
  const style = document.createElement("style");
  style.textContent = `
    html, body {
      touch-action: none !important;
      overscroll-behavior: none !important;
    }
  `;
  document.documentElement.appendChild(style);
})();
EOF

  chown -R "${KIOSK_USER}:${KIOSK_USER}" "$KIOSK_DIR"
  echo "Creata estensione: $EXT_DIR (scope=${EXT_SCOPE})"
}

write_start_script() {
  mkdir -p "$KIOSK_DIR"

  cat > "${KIOSK_DIR}/start-kiosk.sh" <<EOF
#!/bin/bash
set -e

URL="${KIOSK_URL}"
EXT="${EXT_DIR}"
PROFILE="${CHROMIUM_PROFILE}"

# Aspetta rete + sito (evita schermate d'errore / bianco)
until curl -sSf --max-time 2 "\$URL" >/dev/null 2>&1; do
  sleep 1
done

# Avvia Weston in kiosk e poi Chromium
exec /usr/bin/weston --config="${WESTON_INI}" -- \\
  /usr/bin/chromium "\$URL" \\
  --kiosk --no-first-run --noerrdialogs --disable-infobars \\
  --disable-session-crashed-bubble --no-default-browser-check \\
  --password-store=basic --use-mock-keychain \\
  --ozone-platform=wayland \\
  --user-data-dir="\$PROFILE" \\
  --disable-pinch \\
  --disable-features=TouchpadOverscrollHistoryNavigation \\
  --disable-extensions-except="\$EXT" \\
  --load-extension="\$EXT"
EOF

  chmod +x "${KIOSK_DIR}/start-kiosk.sh"
  chown -R "${KIOSK_USER}:${KIOSK_USER}" "$KIOSK_DIR"
  echo "Creato: ${KIOSK_DIR}/start-kiosk.sh"
}

write_systemd_service() {
  local svc="/etc/systemd/system/${SERVICE_NAME}"
  backup_file "$svc"

  cat > "$svc" <<EOF
[Unit]
Description=Kiosk (Weston + Chromium)
After=systemd-user-sessions.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=${KIOSK_USER}
PAMName=login

TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty

# Evita "Opening in existing browser session"
ExecStartPre=-/usr/bin/pkill -u ${KIOSK_USER} chromium
ExecStartPre=-/usr/bin/rm -f ${CHROMIUM_PROFILE}/Singleton*

ExecStart=${KIOSK_DIR}/start-kiosk.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"

  # Kiosk "senza desktop": disabilita getty su tty1 e imposta multi-user
  systemctl disable --now getty@tty1.service || true
  systemctl set-default multi-user.target

  # (opzionale) prova a spegnere eventuali display manager se presenti
  systemctl disable --now lightdm.service 2>/dev/null || true
  systemctl disable --now gdm.service 2>/dev/null || true
  systemctl disable --now sddm.service 2>/dev/null || true

  echo "Creato e abilitato: ${svc}"
}

add_user_groups() {
  # Garantisce accesso DRM/input
  usermod -aG video,render,input "${KIOSK_USER}" || true
  echo "Aggiunto ${KIOSK_USER} ai gruppi: video, render, input"
}

main() {
  require_root
  user_exists || die "Utente '${KIOSK_USER}' non esiste. Crealo (o imposta KIOSK_USER) e rilancia."

  # Validazione rotazione
  case "$ROTATION" in
    rotate-90|rotate-270) ;;
    *) die "ROTATION deve essere rotate-90 oppure rotate-270 (attuale: ${ROTATION})" ;;
  esac

  local dsi_output
  dsi_output="$(detect_dsi_output)"
  echo "Output DSI connesso rilevato: ${dsi_output}"

  install_packages
  update_config_txt
  update_cmdline_quiet
  write_weston_ini "$dsi_output"
  write_no_touch_scroll_extension
  write_start_script
  write_systemd_service
  add_user_groups

  echo
  echo "FATTO."
  echo "Riavvia ora per applicare tutto:"
  echo "  sudo reboot"
}

main "$@"
