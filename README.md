# Deploy su Raspberry Pi 5 (Bookworm)

Questi file automatizzano l'installazione e l'avvio dell'applicazione su un Raspberry Pi 5 con Raspberry Pi OS Bookworm.

## deploy_rpi5.sh

Script di deploy principale che va eseguito come `root` o con `sudo` dalla cartella del progetto clonata sul Raspberry Pi.

Cosa fa:
1. Installa i pacchetti di sistema necessari (Python, git, rsync, curl, ecc.).
2. Crea l'utente di servizio `roomctl` se manca.
3. Sincronizza i sorgenti nella directory `/opt/roomctl`, ignorando `.git`, `.venv` e i file YAML di configurazione esistenti.
4. Crea l'ambiente virtuale Python in `/opt/roomctl/.venv` e installa le dipendenze principali (FastAPI, Uvicorn, ecc.).
5. Copia i file di configurazione di default in `/opt/roomctl/config` senza sovrascrivere quelli già presenti.
6. Installa e abilita il servizio systemd `roomctl.service` e il power scheduler.

Esecuzione tipica:
```bash
sudo ./deploy_rpi5.sh
```
Lo script ricarica systemd e abilita/avvia i servizi; al termine l'applicazione sarà raggiungibile sulla porta 8080.

## config/roomctl.service

Unità systemd che avvia l'applicazione FastAPI con Uvicorn come utente `roomctl`.

Principali impostazioni:
- `WorkingDirectory`: `/opt/roomctl` (dove lo script di deploy copia il progetto).
- `ExecStart`: avvia Uvicorn dall'ambiente virtuale `/opt/roomctl/.venv` esponendo l'app su tutte le interfacce alla porta 8080.
- `Environment`: imposta `ROOMCTL_BASE` a `http://127.0.0.1:8080` (puoi modificarlo in base alle esigenze di rete/reverse proxy).

Lo script di deploy copia automaticamente il file in `/etc/systemd/system/roomctl.service` e lo abilita. Se modifichi l'unità manualmente, ricorda di eseguire `sudo systemctl daemon-reload` e poi `sudo systemctl restart roomctl.service`.# RoomCTL — FastAPI UI (v5 definitivo)

Contenuti e istruzioni nel messaggio della chat.
