# Relazione tecnica dell'applicazione roomctl

## Obiettivi e contesto
La soluzione gestisce l'aula audiovisiva (proiettore PJLink, DSP408 e due relè Shelly) tramite un'app FastAPI con interfaccia touch. L'istanza gira su Raspberry Pi 5 (OS Bookworm) ed espone una UI kiosk locale più API/servizi per le sequenze di accensione, spegnimento e controllo audio/video.

## Architettura logica
- **Framework e server**: FastAPI (`app/main.py`) con Uvicorn come ASGI server, CORS aperto per uso in LAN e websocket per broadcasting dello stato pubblico ogni 2 s.【F:app/main.py†L1-L22】
- **UI**: router `app/ui.py` monta i template Jinja2 e le statiche; gestisce flussi utente (home, area operatore) e inoltra richieste REST verso il backend usando `ROOMCTL_BASE` per indirizzare le API.【F:app/main_ui.py†L1-L7】【F:app/ui.py†L1-L159】
- **API applicative**: router `app/api.py` espone endpoint asincroni per sequenze AV, preset DSP, controllo Shelly, pianificazione power, reboot e sincronizzazione oraria. Le chiamate orchestrano driver specifici (PJLink, DSP408, Shelly) e aggiornano lo stato condiviso.【F:app/api.py†L1-L292】【F:app/api.py†L320-L489】
- **Stato condiviso**: `app/state.py` mantiene un dizionario in memoria con stato di proiettore, DSP, Shelly e messaggi testuali, esposto sia via websocket sia alla UI per feedback immediato.【F:app/state.py†L1-L11】
- **Configurazione dispositivi**: `app/config.py` carica `/opt/roomctl/config/devices.yaml` (override tramite env) e fonde i default per IP, porte e canali; il risultato è importato da UI/API per conoscere la topologia hardware.【F:app/config.py†L1-L41】

## Componenti e responsabilità
- **Autenticazione operatore**: `app/auth.py` valida un PIN (in chiaro o hash Argon2) da `config.yaml`, emette un token memorizzato in cookie `rtoken` e protegge le route dell'area operatore tramite dipendenza FastAPI.【F:app/auth.py†L1-L40】
- **Servizi di power scheduling**: `app/power_schedule.py` normalizza, salva e restituisce la pianificazione di accensione/spegnimento in YAML, con validazione di orari e giorni.【F:app/power_schedule.py†L1-L53】
- **Driver hardware**: `app/drivers/` contiene client per PJLink (proiettore), DSP408 e Shelly HTTP/script; gli endpoint orchestrano combinazioni di questi per scenari predefiniti (accensione proiettore, lezione semplice, spegni aula).【F:app/api.py†L94-L196】【F:app/api.py†L320-L431】
- **Template UI**: `app/templates/index.html` è la home con controlli base, `operator.html` espone i comandi avanzati (mute DSP, recall preset, set date/time, power schedule) e legge lo stato condiviso e i livelli DSP esposti dal backend.【F:app/ui.py†L71-L151】【F:app/ui.py†L173-L279】

## Flussi principali UI → Backend
- **Avvio lezione semplice/video**: i form della UI chiamano handler che impostano un messaggio di stato e inviano POST a `/api/scene/avvio_semplice` o `/api/scene/avvio_proiettore`; il backend attiva relè Shelly, coordina DSP e proiettore e aggiorna lo stato pubblico.【F:app/ui.py†L69-L140】【F:app/api.py†L320-L395】
- **Spegnimento aula**: l'handler `/ui/scene/spegni_aula` richiama `/api/scene/spegni_aula`, che mette in mute il DSP, toglie alimentazione, spegne il proiettore con sequenza ordinata e aggiorna lo stato UI.【F:app/ui.py†L142-L167】【F:app/api.py†L397-L430】
- **Controllo DSP**: l'area operatore usa form per mute, gain/volume step, preset recall e toggle dei canali. Gli handler UI trasformano i form in chiamate verso `/api/dsp/*`, che a loro volta parlano con `DSP408Client` e aggiornano la configurazione persistente per i canali usati.【F:app/ui.py†L217-L272】【F:app/api.py†L240-L314】
- **Gestione pianificazione power**: la UI legge e salva pianificazioni tramite `/api/power/schedule`, che delega a `power_schedule.save_power_schedule` con validazione e persistenza YAML.【F:app/ui.py†L296-L317】【F:app/api.py†L25-L50】【F:app/power_schedule.py†L11-L53】
- **Sincronizzazione oraria e reboot**: l'area operatore invia data/ora e comandi di reboot; gli endpoint backend eseguono `sudo date/hwclock` e pianificano reboot in background per permettere risposta HTTP prima dello stop processo.【F:app/ui.py†L319-L358】【F:app/api.py†L53-L92】

## Stato e comunicazione realtime
- Il websocket `/ws` invia ogni 2 secondi lo stato pubblico (testo, potenza proiettore, input, indicatori DSP/Shelly) ai client UI, evitando polling pesante e mantenendo coerente il banner informativo sul display.【F:app/main.py†L12-L22】【F:app/state.py†L1-L11】

## Deployment e runtime
- **Script di deploy**: `deploy_rpi5.sh` installa pacchetti sistema, crea utente di servizio `roomctl`, sincronizza i sorgenti in `/opt/roomctl`, crea `.venv`, installa dipendenze Python e abilita i servizi systemd.【F:deploy_rpi5.sh†L1-L120】
- **Service unit**: `config/roomctl.service` avvia Uvicorn dal venv su porta 8080, imposta `ROOMCTL_BASE` e usa utente dedicato `roomctl` con working dir `/opt/roomctl`. È predisposto per restart automatico in caso di crash e log verso journald.【F:config/roomctl.service†L1-L32】
- **Configurazioni**: i percorsi YAML di configurazione sono sovrascrivibili via variabili d'ambiente (es. `ROOMCTL_CONFIG`, `ROOMCTL_DEVICES`, `ROOMCTL_UI_CONFIG`). I default di rete/pin vanno adattati in produzione.【F:app/config.py†L1-L41】【F:app/auth.py†L7-L23】【F:app/ui.py†L10-L25】

## Considerazioni di sicurezza e operatività
- PIN con Argon2 e cookie `HttpOnly` per l'area operatore; logout invalida i token in memoria.【F:app/auth.py†L7-L40】
- Comandi sensibili (reboot, set datetime) richiedono permessi elevati lato sistema: l'unità systemd dovrebbe prevedere sudoers sicuro o esecuzione come root secondo le note nei commenti dell'endpoint di reboot.【F:app/api.py†L65-L92】【F:config/roomctl.service†L1-L32】
- Per uso kiosk offline, il browser Chromium viene gestito separatamente (vedi documentazione esistente di hardening) ma la UI espone solo endpoint locali con cookie limitati.

## Funzioni implementate (sintesi)
- Sequenze scenari AV (avvio semplice, avvio proiettore con selezione input, spegnimento aula).【F:app/api.py†L320-L430】
- Controllo puntuale di proiettore (power, input), DSP408 (mute, gain, volume, preset, lettura livelli) e relè Shelly (set/pulse).【F:app/api.py†L199-L314】
- Pianificazione oraria di accensione/spegnimento e gestione RTC/local time.【F:app/api.py†L25-L92】【F:app/power_schedule.py†L11-L53】
- UI touch con home, avvisi di stato, area operatore protetta da PIN, e websocket di stato continuo.【F:app/ui.py†L1-L190】【F:app/main.py†L12-L22】