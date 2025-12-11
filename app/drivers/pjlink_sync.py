from __future__ import annotations
import socket, hashlib, re, time
from typing import Tuple

class PJLinkSocketClient:
    """
    Client PJLink sincrono (blocking) basato su socket.
    - Autenticazione MD5 quando il banner è "PJLINK 1 <nonce>"
    - Timeout e retry configurabili
    - Metodi: get_power(), power(on: bool), set_input("HDMI1"/"HDMI2"/"AUTO")

    Nota auth (spec PJLink): digest = MD5(nonce + password), senza il comando.
    """

    INPUT_MAP = {"HDMI1": "31", "HDMI2": "32", "AUTO": "A0"}

    def __init__(self, host: str, port: int = 4352, password: str = "",
                 timeout: float = 8.0, retries: int = 4):
        self.host = host
        self.port = int(port)
        self.password = password or ""
        self.timeout = float(timeout)
        self.retries = int(retries)

    # ---------- utils ----------
    def _open(self) -> socket.socket:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        return s

    def _readline(self, s: socket.socket) -> str:
        """Legge fino a CR/LF (server invia una riga)."""
        buf = bytearray()
        end = time.time() + self.timeout
        while time.time() < end:
            chunk = s.recv(1)
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"\r") or buf.endswith(b"\n"):
                break
        return buf.decode(errors="ignore").strip()

    def _handshake(self, s: socket.socket) -> Tuple[bool, str]:
        """
        Legge il banner; se non arriva subito, "pungola" con CRLF.
        Ritorna: (need_auth, nonce)
        """
        # alcuni firmware mandano il banner solo dopo input
        try:
            s.sendall(b"\r\n")
        except Exception:
            pass

        banner = ""
        # tentativi multiple letture corte
        t_end = time.time() + max(2.0, self.timeout)
        while time.time() < t_end:
            try:
                line = self._readline(s)
                if line:
                    banner = line
                    break
            except socket.timeout:
                # pungola e riprova
                try:
                    s.sendall(b"\r\n")
                except Exception:
                    pass
            time.sleep(0.1)

        if not banner:
            raise TimeoutError("Timeout in handshake: banner PJLINK assente")

        m = re.match(r"PJLINK\s+(\d)(?:\s+([0-9A-Fa-f]+))?", banner)
        if not m:
            raise RuntimeError(f"Banner PJLINK non valido: {banner}")
        need_auth = (m.group(1) == "1")
        nonce = m.group(2) or ""
        return need_auth, nonce

    def _send_cmd_once(self, cmd: str) -> str:
        """
        Esegue una singola richiesta PJLink: handshake, auth (se serve), invio comando, lettura risposta.
        Ritorna la riga di risposta (senza CR/LF).
        """
        s = self._open()
        try:
            need_auth, nonce = self._handshake(s)
            payload = f"%1{cmd}\r".encode()

            if need_auth:
                if not self.password:
                    raise RuntimeError("PJLink richiede password ma non è configurata.")
                digest = hashlib.md5((nonce + self.password).encode()).hexdigest().encode()
                payload = digest + payload  # prepend digest

            s.sendall(payload)
            resp = self._readline(s)
            return resp
        finally:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            s.close()

    def _send_cmd(self, cmd: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._send_cmd_once(cmd)
            except Exception as e:
                last_exc = e
                # piccolo backoff
                time.sleep(0.4 * (attempt + 1))
        raise last_exc or RuntimeError("Errore PJLink sconosciuto")

    # ---------- API pubblica ----------
    def get_power(self) -> int:
        """
        Ritorna stato alimentazione: 0=Standby, 1=On, 2=Cooling, 3=Warm-up.
        """
        resp = self._send_cmd("POWR ?")
        # atteso: "%1POWR=0/1/2/3" o errori "ERRA"/"ERRx"
        if resp.startswith("%1POWR="):
            try:
                return int(resp.split("=")[1])
            except Exception:
                pass
        if "ERR" in resp:
            raise RuntimeError(f"PJLink POWR? err: {resp}")
        raise RuntimeError(f"PJLink POWR? resp sconosciuta: {resp}")

    def power(self, on: bool) -> bool:
        code = "1" if on else "0"
        resp = self._send_cmd(f"POWR {code}")
        if resp.endswith("=OK"):
            return True
        if "ERR" in resp:
            raise RuntimeError(f"PJLink POWR err: {resp}")
        return False

    def set_input(self, source: str) -> bool:
        source = (source or "HDMI1").upper()
        code = self.INPUT_MAP.get(source)
        if not code:
            raise ValueError(f"Sorgente non valida: {source}")
        resp = self._send_cmd(f"INPT {code}")
        if resp.endswith("=OK"):
            return True
        if "ERR" in resp:
            raise RuntimeError(f"PJLink INPT err: {resp}")
        return False
