# Hardening operativo per Raspberry Pi 5 (LAN isolata)

Questo documento dettaglia come implementare le misure elencate in precedenza su Raspberry Pi 5 con Raspberry Pi OS Bookworm, connesso a una LAN isolata insieme a proiettore, DSP408 e relè Shelly. Ogni sezione include comandi, esempi di configurazione e riferimenti a strumenti open source.

## Accesso e autenticazione
- **Disabilita login root e password per SSH**
  - Modifica `/etc/ssh/sshd_config.d/roomctl.conf`:
    ```
    PermitRootLogin no
    PasswordAuthentication no
    PubkeyAuthentication yes
    KbdInteractiveAuthentication no
    ```
  - Riavvia: `sudo systemctl reload ssh`.
- **Account operativo non-root**
  - Crea utente dedicato (es. `roomctl`) e abilita sudo solo per comandi necessari:
    ```bash
    sudo adduser --disabled-password --gecos "" roomctl
    echo "roomctl ALL=(ALL) NOPASSWD:/usr/bin/systemctl restart roomctl.service" | sudo tee /etc/sudoers.d/roomctl
    sudo chmod 440 /etc/sudoers.d/roomctl
    ```
- **Lock dopo tentativi errati**
  - Installa [fail2ban](https://www.fail2ban.org/): `sudo apt install fail2ban`.
  - Regola locale `/etc/fail2ban/jail.d/ssh.conf`:
    ```
    [sshd]
    enabled = true
    maxretry = 5
    bantime = 1h
    findtime = 15m
    ```
- **Timeout shell e audit storico**
  - Aggiungi a `/etc/profile.d/99-hardening.sh`:
    ```bash
    export TMOUT=600
    readonly TMOUT
    HISTSIZE=2000
    HISTFILESIZE=5000
    export HISTCONTROL=ignoreboth
    ```

## Aggiornamenti e supply-chain
- **Repository offline/mirror**: crea un mirror su macchina connessa e sincronizza via `apt-mirror` o `debmirror`, quindi monta come repository locale (HTTP o file). Configura `/etc/apt/sources.list.d/local.list` con l’URL del mirror.
- **Dipendenze Python con hash**
  - Su macchina connessa: `pip download -r requirements.txt --dest offline-wheelhouse`.
  - Trasferisci la cartella e installa: `pip install --no-index --find-links offline-wheelhouse --require-hashes -r requirements.txt`.
- **Verifica integrità immagini**: conserva checksum SHA256 delle immagini OS e dei pacchetti scaricati, verificando con `sha256sum -c` prima del deploy.

## Cifratura e protezione dei segreti
- **Permessi minimi**
  - Memorizza credenziali in `/etc/roomctl/secret.env` con permessi `600` e proprietario `roomctl:roomctl`.
- **Niente core dump**
  - `/etc/security/limits.d/nocore.conf`:
    ```
    * hard core 0
    ```
  - `/etc/sysctl.d/99-coredump.conf`:
    ```
    fs.suid_dumpable = 0
    ```
  - Applica: `sudo sysctl --system`.

## Servizi e superficie d’attacco
- **Rimuovi servizi non necessari**: disabilita `avahi-daemon`, `bluetooth`, `cups`, servizi seriali se inutili:
  ```bash
  sudo systemctl disable --now avahi-daemon.service bluetooth.service cups.service hciuart.service
  ```
- **Firewall minimale con nftables**
  - Installa: `sudo apt install nftables`.
  - Regole in `/etc/nftables.conf` limitate alla porta applicativa (es. 8000) sulla LAN isolata:
    ```nft
    table inet filter {
      chain input {
        type filter hook input priority 0;
        ct state established,related accept
        iif lo accept
        ip saddr 192.168.10.0/24 tcp dport {22,8000} accept
        counter drop
      }
    }
    ```
  - Abilita: `sudo systemctl enable --now nftables`.
- **Bind locale dell’app**
  - Nel servizio systemd di Uvicorn (es. `config/roomctl.service`), usa `--host 192.168.10.2` invece di `0.0.0.0`.
- **Sandbox systemd**
  - Aggiungi opzioni di isolamento al servizio:
    ```ini
    PrivateTmp=yes
    ProtectSystem=strict
    ProtectHome=yes
    NoNewPrivileges=yes
    ReadWritePaths=/opt/roomctl/var
    DevicePolicy=closed
    DeviceAllow=/dev/input/event* r
    RestrictAddressFamilies=AF_INET
    ```
  - Verifica con `systemd-analyze security roomctl.service`.
- **Log controllati**
  - Configura journald `/etc/systemd/journald.conf`:
    ```
    SystemMaxUse=200M
    RuntimeMaxUse=100M
    Storage=persistent
    ```

## Fisico e console
- **Boot protetto**: disabilita boot da USB impostando `PROGRAM_USB_BOOT_MODE=0` in `rpi-eeprom-config` e bloccando con `rpi-eeprom-config --out config` + `rpi-eeprom-update -d -f config`.
- **TTY limitati**
  - Disabilita console seriale: rimuovi `console=serial0,115200` da `/boot/firmware/cmdline.txt` e disabilita `serial-getty@serial0.service`.
  - Riduci login locali mantenendo solo `tty1`: `sudo systemctl disable --now getty@tty2.service getty@tty3.service getty@tty4.service`.
- **USB device control**
  - Installa [usbguard](https://usbguard.github.io/): `sudo apt install usbguard`.
  - Genera policy: `sudo usbguard generate-policy > /etc/usbguard/rules.conf`; metti in modalità `Block` e autorizza solo il touchscreen.

## Integrità e auditing
- **auditd**
  - Installa: `sudo apt install auditd audispd-plugins`.
  - Regole minime `/etc/audit/rules.d/roomctl.rules`:
    ```
    -w /opt/roomctl -p wa -k roomctl_app
    -w /etc/systemd/system/roomctl.service -p wa -k roomctl_service
    -w /etc/roomctl -p wa -k roomctl_config
    ```
  - Applica: `sudo augenrules --load`.
- **Verifica file con AIDE**
  - Installa [AIDE](https://aide.github.io/): `sudo apt install aide`.
  - Inizializza DB: `sudo aideinit`; sostituisci `/var/lib/aide/aide.db.new` con `aide.db`.
  - Esegui controlli periodici con `sudo aide --check` e pianifica con `cron`/`systemd timers`.

## Rete interna
- **IP e DHCP statici**: configura indirizzi statici per Raspberry e dispositivi nel DHCP server (se presente) o in `/etc/dhcpcd.conf`.
- **Segmentazione**: se disponibile uno switch gestito, crea VLAN separate per gestione e traffico AV; collega il Raspberry solo alle VLAN necessarie.
- **Disabilita IPv6 se non usato**
  - `/etc/sysctl.d/99-disable-ipv6.conf`:
    ```
    net.ipv6.conf.all.disable_ipv6 = 1
    net.ipv6.conf.default.disable_ipv6 = 1
    ```
  - Applica: `sudo sysctl --system`.

## Backup e recovery
- **Snapshot periodici**
  - Usa [rpi-clone](https://github.com/billw2/rpi-clone) o `dd` per clonare su SD/SSD esterna: `sudo rpi-clone -f /dev/sda`.
  - Conserva copie offline etichettate con data e checksum.
- **Procedura di ripristino**
  - Tieni uno script di post-restore (es. `restore.sh`) che ripristina `/etc/roomctl`, il servizio systemd e reinstalla i pacchetti dal mirror offline.

## Applicazione e dipendenze
- **Esecuzione con utente dedicato**: assicurati che il servizio app usi `User=roomctl` e abbia accesso solo a `/opt/roomctl` e `/etc/roomctl`.
- **Validazione input**: aggiungi controlli lato server per input da touchscreen; nelle API FastAPI usa `pydantic` con tipi e `constr`/`conint` per limiti.
- **Messaggi di errore ridotti**: configura uvicorn/gunicorn con `--proxy-headers --log-level warning` e gestisci eccezioni con handler che non espongono stack trace agli utenti.

## Browser Chromium in modalità kiosk
- **Utente dedicato e autologin minimo**
  - Crea un utente senza sudo (es. `kiosk`): `sudo adduser --disabled-password kiosk`.
  - Abilita l’autologin su `tty1` solo per `kiosk`: crea `/etc/systemd/system/getty@tty1.service.d/autologin.conf`:
    ```ini
    [Service]
    ExecStart=
    ExecStart=-/sbin/agetty --autologin kiosk --noclear %I $TERM
    ```
- **Lanciatore con profilo ephemero**
  - Crea il servizio utente `~kiosk/.config/systemd/user/chromium-kiosk.service`:
    ```ini
    [Unit]
    Description=Chromium kiosk
    After=graphical-session.target

    [Service]
    Type=simple
    ExecStart=/usr/bin/chromium --kiosk --app=http://127.0.0.1:8080 \
      --noerrdialogs --disable-session-crashed-bubble --disable-infobars \
      --no-first-run --incognito --disable-sync --disable-translate \
      --overscroll-history-navigation=0 --disable-features=Translate,Autofill,PasswordManagerOnboarding \
      --password-store=basic --check-for-update-interval=31536000
    Restart=on-failure
    Environment=DISPLAY=:0 XAUTHORITY=/home/kiosk/.Xauthority

    [Install]
    WantedBy=default.target
    ```
  - Abilita e avvia: `sudo -u kiosk systemctl --user enable --now chromium-kiosk.service`.
- **Policy gestite per bloccare funzioni non necessarie**
  - Installa policy managed in `/etc/chromium/policies/managed/kiosk.json`:
    ```json
    {
      "HomepageLocation": "http://127.0.0.1:8080",
      "HomepageIsNewTabPage": false,
      "RestoreOnStartup": 4,
      "RestoreOnStartupURLs": ["http://127.0.0.1:8080"],
      "PopupBlockingEnabled": true,
      "DefaultSearchProviderEnabled": false,
      "PasswordManagerEnabled": false,
      "DeveloperToolsDisabled": true,
      "ExtensionInstallBlocklist": ["*"]
    }
    ```
- **Limitazioni di tastiera e uscita**
  - Disabilita combinazioni di uscita (Alt+F4, Alt+Tab) per l’utente `kiosk` con [xmodmap](https://www.x.org/archive/X11R7.7/doc/man/man1/xmodmap.1.xhtml) o [xcape](https://github.com/alols/xcape), aggiungendo in `~kiosk/.xsessionrc`:
    ```bash
    xmodmap -e "keycode 37 ="  # disabilita Ctrl_L (adatta alle tue keymap)
    xmodmap -e "keycode 64 ="  # disabilita Alt_L
    ```
  - Impedisci l’accesso a TTY extra disabilitando `getty@tty2.service` ecc. (vedi sezione "Fisico e console").
- **Schermo e privacy**
  - Nascondi il cursore se non serve usando [unclutter](https://wiki.archlinux.org/title/Unclutter): `sudo apt install unclutter` e avvia con `unclutter -idle 0` nel profilo `kiosk`.
  - Imposta il blocco schermo automatico tramite `xset s 300` se desideri un timeout; altrimenti disabilita il DPMS solo se necessario: `xset -dpms s off`.
- **Log e ripartenza**
  - Log del servizio utente: `sudo -u kiosk journalctl --user -u chromium-kiosk`.
  - Garantire restart automatico dopo crash/aggiornamenti di configurazione con `Restart=on-failure` e `systemctl --user restart chromium-kiosk` nel post-deploy.

## Operatività
- **Sudo protetto**: rimuovi regole `NOPASSWD` non indispensabili e verifica con `sudo -l` per l’utente operativo.
- **Finestra di manutenzione offline**: definisci una checklist (aggiornamento mirror, sincronizzazione pacchetti, riavvio, verifica `systemctl status roomctl` e `journalctl -u roomctl`).
- **Registro delle modifiche**: mantieni un changelog manuale in `/var/log/roomctl-change.log` aggiornato via `sudo tee -a /var/log/roomctl-change.log` dopo ogni intervento.# Hardening operativo per Raspberry Pi 5 (LAN isolata)

Questo documento dettaglia come implementare le misure elencate in precedenza su Raspberry Pi 5 con Raspberry Pi OS Bookworm, connesso a una LAN isolata insieme a proiettore, DSP408 e relè Shelly. Ogni sezione include comandi, esempi di configurazione e riferimenti a strumenti open source.

## Accesso e autenticazione
- **Disabilita login root e password per SSH**
  - Modifica `/etc/ssh/sshd_config.d/roomctl.conf`:
    ```
    PermitRootLogin no
    PasswordAuthentication no
    PubkeyAuthentication yes
    KbdInteractiveAuthentication no
    ```
  - Riavvia: `sudo systemctl reload ssh`.
- **Account operativo non-root**
  - Crea utente dedicato (es. `roomctl`) e abilita sudo solo per comandi necessari:
    ```bash
    sudo adduser --disabled-password --gecos "" roomctl
    echo "roomctl ALL=(ALL) NOPASSWD:/usr/bin/systemctl restart roomctl.service" | sudo tee /etc/sudoers.d/roomctl
    sudo chmod 440 /etc/sudoers.d/roomctl
    ```
- **Lock dopo tentativi errati**
  - Installa [fail2ban](https://www.fail2ban.org/): `sudo apt install fail2ban`.
  - Regola locale `/etc/fail2ban/jail.d/ssh.conf`:
    ```
    [sshd]
    enabled = true
    maxretry = 5
    bantime = 1h
    findtime = 15m
    ```
- **Timeout shell e audit storico**
  - Aggiungi a `/etc/profile.d/99-hardening.sh`:
    ```bash
    export TMOUT=600
    readonly TMOUT
    HISTSIZE=2000
    HISTFILESIZE=5000
    export HISTCONTROL=ignoreboth
    ```

## Aggiornamenti e supply-chain
- **Repository offline/mirror**: crea un mirror su macchina connessa e sincronizza via `apt-mirror` o `debmirror`, quindi monta come repository locale (HTTP o file). Configura `/etc/apt/sources.list.d/local.list` con l’URL del mirror.
- **Dipendenze Python con hash**
  - Su macchina connessa: `pip download -r requirements.txt --dest offline-wheelhouse`.
  - Trasferisci la cartella e installa: `pip install --no-index --find-links offline-wheelhouse --require-hashes -r requirements.txt`.
- **Verifica integrità immagini**: conserva checksum SHA256 delle immagini OS e dei pacchetti scaricati, verificando con `sha256sum -c` prima del deploy.

## Cifratura e protezione dei segreti
- **Permessi minimi**
  - Memorizza credenziali in `/etc/roomctl/secret.env` con permessi `600` e proprietario `roomctl:roomctl`.
- **Niente core dump**
  - `/etc/security/limits.d/nocore.conf`:
    ```
    * hard core 0
    ```
  - `/etc/sysctl.d/99-coredump.conf`:
    ```
    fs.suid_dumpable = 0
    ```
  - Applica: `sudo sysctl --system`.

## Servizi e superficie d’attacco
- **Rimuovi servizi non necessari**: disabilita `avahi-daemon`, `bluetooth`, `cups`, servizi seriali se inutili:
  ```bash
  sudo systemctl disable --now avahi-daemon.service bluetooth.service cups.service hciuart.service
  ```
- **Firewall minimale con nftables**
  - Installa: `sudo apt install nftables`.
  - Regole in `/etc/nftables.conf` limitate alla porta applicativa (es. 8000) sulla LAN isolata:
    ```nft
    table inet filter {
      chain input {
        type filter hook input priority 0;
        ct state established,related accept
        iif lo accept
        ip saddr 192.168.10.0/24 tcp dport {22,8000} accept
        counter drop
      }
    }
    ```
  - Abilita: `sudo systemctl enable --now nftables`.
- **Bind locale dell’app**
  - Nel servizio systemd di Uvicorn (es. `config/roomctl.service`), usa `--host 192.168.10.2` invece di `0.0.0.0`.
- **Sandbox systemd**
  - Aggiungi opzioni di isolamento al servizio:
    ```ini
    PrivateTmp=yes
    ProtectSystem=strict
    ProtectHome=yes
    NoNewPrivileges=yes
    ReadWritePaths=/opt/roomctl/var
    DevicePolicy=closed
    DeviceAllow=/dev/input/event* r
    RestrictAddressFamilies=AF_INET
    ```
  - Verifica con `systemd-analyze security roomctl.service`.
- **Log controllati**
  - Configura journald `/etc/systemd/journald.conf`:
    ```
    SystemMaxUse=200M
    RuntimeMaxUse=100M
    Storage=persistent
    ```

## Fisico e console
- **Boot protetto**: disabilita boot da USB impostando `PROGRAM_USB_BOOT_MODE=0` in `rpi-eeprom-config` e bloccando con `rpi-eeprom-config --out config` + `rpi-eeprom-update -d -f config`.
- **TTY limitati**
  - Disabilita console seriale: rimuovi `console=serial0,115200` da `/boot/firmware/cmdline.txt` e disabilita `serial-getty@serial0.service`.
  - Riduci login locali mantenendo solo `tty1`: `sudo systemctl disable --now getty@tty2.service getty@tty3.service getty@tty4.service`.
- **USB device control**
  - Installa [usbguard](https://usbguard.github.io/): `sudo apt install usbguard`.
  - Genera policy: `sudo usbguard generate-policy > /etc/usbguard/rules.conf`; metti in modalità `Block` e autorizza solo il touchscreen.

## Integrità e auditing
- **auditd**
  - Installa: `sudo apt install auditd audispd-plugins`.
  - Regole minime `/etc/audit/rules.d/roomctl.rules`:
    ```
    -w /opt/roomctl -p wa -k roomctl_app
    -w /etc/systemd/system/roomctl.service -p wa -k roomctl_service
    -w /etc/roomctl -p wa -k roomctl_config
    ```
  - Applica: `sudo augenrules --load`.
- **Verifica file con AIDE**
  - Installa [AIDE](https://aide.github.io/): `sudo apt install aide`.
  - Inizializza DB: `sudo aideinit`; sostituisci `/var/lib/aide/aide.db.new` con `aide.db`.
  - Esegui controlli periodici con `sudo aide --check` e pianifica con `cron`/`systemd timers`.

## Rete interna
- **IP e DHCP statici**: configura indirizzi statici per Raspberry e dispositivi nel DHCP server (se presente) o in `/etc/dhcpcd.conf`.
- **Segmentazione**: se disponibile uno switch gestito, crea VLAN separate per gestione e traffico AV; collega il Raspberry solo alle VLAN necessarie.
- **Disabilita IPv6 se non usato**
  - `/etc/sysctl.d/99-disable-ipv6.conf`:
    ```
    net.ipv6.conf.all.disable_ipv6 = 1
    net.ipv6.conf.default.disable_ipv6 = 1
    ```
  - Applica: `sudo sysctl --system`.

## Backup e recovery
- **Snapshot periodici**
  - Usa [rpi-clone](https://github.com/billw2/rpi-clone) o `dd` per clonare su SD/SSD esterna: `sudo rpi-clone -f /dev/sda`.
  - Conserva copie offline etichettate con data e checksum.
- **Procedura di ripristino**
  - Tieni uno script di post-restore (es. `restore.sh`) che ripristina `/etc/roomctl`, il servizio systemd e reinstalla i pacchetti dal mirror offline.

## Applicazione e dipendenze
- **Esecuzione con utente dedicato**: assicurati che il servizio app usi `User=roomctl` e abbia accesso solo a `/opt/roomctl` e `/etc/roomctl`.
- **Validazione input**: aggiungi controlli lato server per input da touchscreen; nelle API FastAPI usa `pydantic` con tipi e `constr`/`conint` per limiti.
- **Messaggi di errore ridotti**: configura uvicorn/gunicorn con `--proxy-headers --log-level warning` e gestisci eccezioni con handler che non espongono stack trace agli utenti.

## Operatività
- **Sudo protetto**: rimuovi regole `NOPASSWD` non indispensabili e verifica con `sudo -l` per l’utente operativo.
- **Finestra di manutenzione offline**: definisci una checklist (aggiornamento mirror, sincronizzazione pacchetti, riavvio, verifica `systemctl status roomctl` e `journalctl -u roomctl`).
- **Registro delle modifiche**: mantieni un changelog manuale in `/var/log/roomctl-change.log` aggiornato via `sudo tee -a /var/log/roomctl-change.log` dopo ogni intervento.