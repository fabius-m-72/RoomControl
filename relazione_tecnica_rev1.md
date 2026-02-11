# Relazione tecnica dell'applicazione roomctl

## 1. Hardware utilizzato
- **Single board computer**: Raspberry Pi 5 con Raspberry Pi OS (Bookworm/Trixie) come nodo di controllo centrale; ospita l'app FastAPI, il browser kiosk Chromium e i servizi systemd di scheduling e UI touch.【F:deploy_rpi5.sh†L1-L241】
- **Proiettore con protocollo PJLink**: controllato via TCP con parametri host/porta/password definiti in `devices.yaml`.【F:config/devices.yaml†L13-L19】
- **DSP audio (DSP408)**: gestito via rete (host/porta) e con mapping di ingressi/uscite configurato in `devices.yaml`.【F:config/devices.yaml†L1-L12】
- **Relè Shelly**: due unità Shelly con base URL e canali configurati per alimentazione proiettore/DSP e attuazioni accessorie (inclusa inversione corsa se richiesta).【F:config/devices.yaml†L20-L30】
- **RTC**: utilizzato per pianificare accensioni/spegnimenti; il deploy configura la ricarica batteria RTC e lo scheduler programma il wakealarm del dispositivo di sistema.【F:deploy_rpi5.sh†L168-L175】【F:config/power_scheduler.py†L3-L171】

## 2. Architettura dell'applicazione
- **Backend**: FastAPI (`app/main.py`) con Uvicorn, WebSocket `/ws` per broadcast dello stato ogni 2 secondi e routing modulare per UI/API.【F:app/main.py†L1-L22】
- **UI touch/kiosk**: router `app/ui.py` serve template Jinja2 e gestisce le azioni utente (home e area operatore), inoltrando le richieste alle API interne; Chromium gira in modalità kiosk come servizio systemd dedicato e punta a `http://127.0.0.1:8080`.【F:app/ui.py†L1-L358】【F:deploy_rpi5.sh†L175-L241】
- **API applicative**: `app/api.py` espone endpoint per scenari AV, controllo proiettore (PJLink), DSP408 e Shelly, oltre a power scheduling, reboot e sincronizzazione oraria.【F:app/api.py†L1-L516】
- **Stato condiviso**: `app/state.py` mantiene lo stato in memoria (proiettore, DSP, Shelly, messaggi) usato da UI e websocket per feedback in tempo reale.【F:app/state.py†L1-L11】
- **Configurazione dinamica**: `app/config.py` carica i file YAML da `/opt/roomctl/config` (override tramite variabili d’ambiente) per dispositivi e impostazioni di UI/autenticazione.【F:app/config.py†L1-L41】

## 3. Principali funzionalità software
- **Scenari di accensione/spegnimento**: sequenze AV per accendere proiettore e DSP, selezionare input, gestire relè e spegnere l’aula in modo ordinato.【F:app/api.py†L320-L516】
- **Controllo puntuale dispositivi**:
  - Proiettore via PJLink (power/input).【F:app/api.py†L199-L266】【F:app/drivers/pjlink.py†L1-L113】
  - DSP408 (mute, gain, volume, preset, lettura livelli).【F:app/api.py†L265-L371】【F:app/drivers/dsp408.py†L206-L309】
  - Shelly Gen2/Pro con chiamate RPC o script (relè e cover).【F:app/api.py†L382-L516】【F:app/drivers/shelly_http.py†L1-L197】
- **Area operatore protetta**: PIN configurabile (in chiaro o hash Argon2) con cookie `rtoken` per proteggere le rotte operative.【F:app/auth.py†L1-L40】
- **Pianificazione accensione/spegnimento**: gestione via UI/API e persistenza in YAML con validazione di giorni/orari.【F:app/api.py†L25-L50】【F:app/power_schedule.py†L1-L53】
- **Sincronizzazione data/ora e reboot controllato**: endpoint per aggiornare RTC e riavviare il sistema con esecuzione asincrona del reboot per evitare timeout HTTP.【F:app/api.py†L53-L92】

## 4. Scelte progettuali sui package Python
- **FastAPI**: scelto per API asincrone e router modulari (UI/API/auth) con dipendenze per autenticazione e gestione dello stato.【F:deploy_rpi5.sh†L73-L80】【F:app/main.py†L1-L22】
- **Uvicorn**: server ASGI leggero per produzione locale in modalità kiosk e servizio systemd dedicato (`roomctl.service`).【F:deploy_rpi5.sh†L73-L80】【F:config/roomctl.service†L1-L22】
- **httpx / requests**: usati per comunicazioni HTTP verso dispositivi esterni e driver (Shelly) con gestione errori lato API.【F:deploy_rpi5.sh†L73-L80】【F:app/drivers/shelly_http.py†L1-L197】
- **PyYAML**: per leggere e salvare configurazioni e pianificazioni (`devices.yaml`, `power_schedule.yaml`).【F:deploy_rpi5.sh†L73-L80】【F:app/power_schedule.py†L1-L53】
- **python-multipart**: necessario per form HTML nelle route UI che inviano dati via `POST` (es. mute/preset DSP).【F:deploy_rpi5.sh†L73-L80】【F:app/ui.py†L173-L279】
- **Jinja2**: templating per la UI touch server-side, con layout home e area operatore.【F:deploy_rpi5.sh†L73-L80】【F:app/ui.py†L1-L190】

## 5. Descrizione dei file YAML di configurazione
- `config/config.yaml`: contiene il PIN di accesso all’area operatore (in chiaro o hash Argon2).【F:config/config.yaml†L1】
- `config/devices.yaml`: definisce la topologia hardware (IP/porte PJLink, DSP408, Shelly, mapping canali).【F:config/devices.yaml†L1-L30】
- `config/ui.yaml`: opzioni di visualizzazione per la UI kiosk (es. `show_combined`).【F:config/ui.yaml†L1】
- `config/power_schedule.yaml`: pianificazione di accensione/spegnimento con giorni attivi, orari `on_time/off_time` e flag `enabled`.【F:config/power_schedule.yaml†L1-L6】

## 6. Funzione power scheduling
- **Salvataggio e validazione**: `app/power_schedule.py` normalizza i formati orari, valida giorni e persiste il YAML usato dalla UI operatore.【F:app/power_schedule.py†L1-L53】
- **Esecuzione on-boot/notturna**: `config/power_scheduler.py` legge `power_schedule.yaml`, calcola il prossimo evento valido, programma l’accensione tramite `rtcwake` e pianifica lo spegnimento con `systemd-run`.【F:config/power_scheduler.py†L3-L218】
- **Timer systemd**: il timer `roomctl-power-scheduler.timer` richiama il servizio `roomctl-power-scheduler.service` all’avvio e ogni notte alle 03:00 per aggiornare il wakealarm e gli shutdown programmati.【F:config/roomctl-power-scheduler.timer†L1-L9】【F:config/roomctl-power-scheduler.service†L1-L9】

## 7. Installazione dell’applicazione su nuovo hardware
### 7.1 Procedura consigliata (Raspberry Pi 5)
1. **Clona il repository** sul Raspberry connesso alla LAN AV.
2. **Esegui lo script di deploy** come `root` o via `sudo`:
   - Installa dipendenze di sistema (Python, git, Chromium, Xorg, ecc.).
   - Crea l’utente di servizio `roomctl`.
   - Copia l’app in `/opt/roomctl`, crea il virtualenv e installa i package Python.
   - Copia i file YAML di default senza sovrascrivere configurazioni esistenti.
   - Configura il boot (schermata nera, rotazione touch, ricarica RTC).
   - Abilita la modalità kiosk via servizio systemd `kiosk.service`.
   - Installa e abilita `roomctl.service` e il power scheduler.
   - Comando tipico:
     ```bash
     sudo ./deploy_rpi5.sh
     ```
   【F:deploy_rpi5.sh†L1-L241】

3. **Power scheduler**: lo script richiama `config/install_power_scheduler.sh`, che copia lo scheduler in `/opt/roomctl/config`, installa le unità systemd e abilita il timer; infine esegue subito lo scheduler per programmare il prossimo evento.【F:deploy_rpi5.sh†L252-L269】【F:config/install_power_scheduler.sh†L1-L33】

### 7.2 Script e unità di servizio coinvolte
- **`deploy_rpi5.sh`**: orchestratore completo di installazione e configurazione (OS, kiosk, servizi).【F:deploy_rpi5.sh†L1-L269】
- **`config/roomctl.service`**: unità systemd che avvia Uvicorn con variabili d’ambiente e working dir `/opt/roomctl`.【F:config/roomctl.service†L1-L22】
- **`config/install_power_scheduler.sh`**: installazione iniziale dello scheduler e attivazione timer systemd.【F:config/install_power_scheduler.sh†L1-L33】
- **`config/roomctl-power-scheduler.timer`**: esecuzione all’avvio e ogni notte.【F:config/roomctl-power-scheduler.timer†L1-L9】

### 7.3 Ottenere un terminale funzionante
- Il deploy abilita il kiosk su `tty1` e indica le combinazioni di uscita consigliate. Per accedere a un terminale:
  - Usa `Ctrl+Alt+F2` per passare a una console virtuale e fare login con un utente amministratore.
  - Per sospendere temporaneamente il kiosk: `sudo systemctl stop kiosk.service`.
  - Per riattivarlo: `sudo systemctl restart kiosk.service`.
  - Per disabilitare l’autostart di Chromium: `sudo systemctl disable --now kiosk.service`.
  - Log utili: `journalctl -b -u kiosk.service` e `/home/kiosk/.local/share/kiosk-xorg.log`.
  【F:deploy_rpi5.sh†L14-L18】

## 8. Comandi utili per manutenzione e aggiornamento
### 8.1 Manutenzione operativa
- **Fermare/riavviare Chromium kiosk**:
  - `sudo systemctl stop kiosk.service`
  - `sudo systemctl restart kiosk.service`
  - `sudo systemctl disable --now kiosk.service` (disabilita autostart)
  【F:deploy_rpi5.sh†L14-L18】
- **Verificare lo stato dei servizi**:
  - `systemctl status roomctl.service`
  - `systemctl status roomctl-power-scheduler.timer`
  - `systemctl status roomctl-power-scheduler.service`
  【F:config/roomctl.service†L1-L22】【F:config/roomctl-power-scheduler.timer†L1-L9】
- **Log utili**:
  - `journalctl -b -u roomctl.service`
  - `journalctl -b -u kiosk.service`
  - `journalctl -b -u roomctl-power-scheduler.service`
  【F:deploy_rpi5.sh†L14-L18】

### 8.2 Aggiornamento e accesso remoto
- **Aggiornare il codice**: eseguire un nuovo deploy con `sudo ./deploy_rpi5.sh` per risincronizzare `/opt/roomctl` e reinstallare dipendenze se necessario.【F:deploy_rpi5.sh†L1-L241】
- **Accesso via SSH su rete Ethernet**: abilitare SSH sul Raspberry e collegarsi da una workstation della LAN AV, quindi usare `scp`/`rsync` per trasferire file di configurazione o log (modalità standard di manutenzione remoto).
- **Verifica pianificazione power**: `sudo /usr/bin/python3 -u /opt/roomctl/config/power_scheduler.py --status` (stampa stato YAML + timer) per diagnosi rapida dello scheduler.【F:config/power_scheduler.py†L23-L215】