# app/drivers/shelly_http.py
from __future__ import annotations
import asyncio
from typing import Union
import httpx
import requests

class ShellyHTTP:
    """
    Driver HTTP minimale per Shelly Gen2 (RPC).
    Accetta base/base_url/host, ad es:
      ShellyHTTP(base="http://192.168.1.51")
      ShellyHTTP(base_url="http://192.168.1.51")
      ShellyHTTP(host="192.168.1.51")
    """
    def __init__(self, base: str | None = None, base_url: str | None = None, host: str | None = None, timeout: float = 5.0):
        base_url = base_url or base or (f"http://{host}" if host else None)
        if not base_url:
            raise ValueError("ShellyHTTP: specifica base/base_url o host")
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    async def set_relay(self, relay: Union[int, str], on: bool) -> bool:
        """Accendi/Spegni canale: /rpc/Switch.Set {id, on}"""
        url = f"{self.base}/rpc/Switch.Set"
        payload = {"id": int(relay), "on": bool(on)}
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=payload)
            return r.status_code == 200



    async def pulse(self, relay: Union[int, str], ms: int = 500) -> bool:
        """
        Impulso semplice: ON -> sleep -> OFF.
        Se il tuo impianto usa logica diversa, adatta qui.
        """
        ok1 = await self.set_relay(relay, True)
        await asyncio.sleep(max(ms, 0) / 1000.0)
        ok2 = await self.set_relay(relay, False)
        return ok1 and ok2

class ShellyHTTP_script:
    """
    Driver HTTP minimale per Shelly Gen2 (RPC).
    Accetta base/base_url/host, ad es:
      ShellyHTTP(base="http://192.168.1.51")
      ShellyHTTP(base_url="http://192.168.1.51")
      ShellyHTTP(host="192.168.1.51")
    """
    def __init__(self, base: str | None = None, base_url: str | None = None, host: str | None = None, timeout: float = 5.0):
        base_url = base_url or base or (f"http://{host}" if host else None)
        if not base_url:
            raise ValueError("ShellyHTTP: specifica base/base_url o host")
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def projct_off_main(self) -> bool:
        """Spegnimento ritardato proiettore http://IP_DEL_SHELLY/rpc/Script.Start?id=1"""
        url = "http://192.168.1.10/rpc/Script.Start?id=1"
        r =  requests.get(url, timeout=self.timeout)
        return r.status_code == 200

    def shelly_pro2pm_cover(self,
        ip: str='192.168.1.11',
        action: str | None = None,
        cover_id: int = 0,
        position: int | None = None,
        duration: float | None = None,
        timeout: float = 5.0,
        username: str | None = None,
        password: str | None = None,
        inverti_corsa: bool = False,
    ):
        """
        Comanda uno Shelly Pro 2PM configurato in modalità 'cover' tramite HTTP RPC.

        Parametri
        ---------
        ip : str
            Indirizzo IP del dispositivo (es. '192.168.1.50').
        action : str
            Azione da eseguire: 'open', 'close', 'stop', 'position', 'status'.
        cover_id : int, opzionale
            ID della cover (0 o 1 di solito). Default 0.
        position : int, opzionale
            Posizione in % per l'azione 'position' (0-100).
        duration : float, opzionale
            Durata in secondi per 'open' / 'close' se vuoi movimento temporizzato.
        timeout : float, opzionale
            Timeout della richiesta HTTP in secondi. Default 5.0.
        username, password : str, opzionale
            Credenziali HTTP basic auth se configurate sullo Shelly.

        Ritorna
        -------
        dict
            Risposta JSON del dispositivo (es. stato corrente).
        """
        base = f"http://{ip}"

        action = action.lower()
        if inverti_corsa and action in {"open", "close"}:
            action = "close" if action == "open" else "open"

        if action == "status":
            # Legge lo stato attuale della cover
            path = f"/rpc/Cover.GetStatus?id={cover_id}"

        elif action == "open":
            # Apertura (con opzionale durata)
            path = f"/rpc/Cover.Open?id={cover_id}"
            if duration is not None:
                path += f"&duration={duration}"

        elif action == "close":
            # Chiusura (con opzionale durata)
            path = f"/rpc/Cover.Close?id={cover_id}"
            if duration is not None:
                path += f"&duration={duration}"

        elif action == "stop":
            # Stop immediato
            path = f"/rpc/Cover.Stop?id={cover_id}"

        elif action in ("position", "goto", "goto_position"):
            # Vai a una certa posizione %
            if position is None:
                raise ValueError("Per l'azione 'position' devi specificare 'position' (0-100).")
            if not (0 <= position <= 100):
                raise ValueError("La posizione deve essere compresa tra 0 e 100.")
            path = f"/rpc/Cover.GoToPosition?id={cover_id}&pos={position}"

        else:
            raise ValueError(
                "Azione non valida. Usa: 'open', 'close', 'stop', 'position', 'status'."
            )

        url = base + path
        #print(url)#---------------------------------------------------------------------
        try:
            if username and password:
                resp = requests.get(url, timeout=timeout, auth=(username, password))
            else:
                resp = requests.get(url, timeout=timeout)
                 
            resp.raise_for_status()
            #print(resp,"---",resp.raise_for_status())
        except requests.RequestException as e:
            raise ShellyCoverError(f"Errore nella richiesta a {url}: {e}") from e

        # quasi tutte le RPC tornano JSON
        try:
            #print(resp.json(),"---",resp.text)
            return {'ok':True} #resp.json()
        except ValueError:
            # se per qualche motivo non torna JSON
            return {'ok':True} #{"raw": resp.text}

class ShellyCoverError(Exception):
    """Errore generico per comandi verso Shelly in modalità cover."""
    pass
