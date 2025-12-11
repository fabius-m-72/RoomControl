#app/drivers/pjlink.py
import asyncio, hashlib, re
from typing import Optional

class PJLinkClient:
    def __init__(self, host: str="192.168.1.200", port: int = 4352, password: str = "1234", timeout: float = 8.0, retries: int = 4):
        self.host = host
        self.port = port
        self.password = password or ""
        self.timeout = timeout
        self.retries = retries
        # mappa sorgenti: adatta se il tuo modello usa codici diversi
        self.input_map = {"Computer1": "11","Computer2": "12", "HDMI1": "32", "HDMI2": "33", "HDBaseT": "56"}

    async def _open(self):
        return await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)

    async def _handshake(self, r: asyncio.StreamReader, w: asyncio.StreamWriter):
        # Legge il banner: "PJLINK 0" oppure "PJLINK 1 xxxxxxxx\r"
        banner = await asyncio.wait_for(r.readuntil(b"\r"), self.timeout)
        text = banner.decode(errors="ignore").strip()
        # print("PJLINK banner:", text)
        m = re.match(r"PJLINK\s+(\d)(?:\s+([0-9A-Fa-f]+))?", text)
        if not m:
            raise RuntimeError(f"Banner PJLINK non valido: {text}")
        need_auth = m.group(1) == "1"
        rand = m.group(2) or ""
        return need_auth, rand

    async def _send_cmd(self, cmd: str) -> str:
        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                r, w = await self._open()
                w.write(b"\r"); await w.drain()
                need_auth, rand = await self._handshake(r, w)

                # Comando in formato PJLink: %1<cmd>\r
                payload = f"%1{cmd}\r"

                if need_auth:
                    if not self.password:
                        w.close()
                        await w.wait_closed()
                        raise RuntimeError("PJLink richiede password ma non è configurata.")
                    # MD5( rand + password + payload_senza_CR )
                    to_hash = (rand + self.password).encode()
                    auth = hashlib.md5(to_hash).hexdigest()
                    payload = auth + payload  # prefisso con hash

                w.write(payload.encode())
                await w.drain()
                # risposta termina con \r
                resp = await asyncio.wait_for(r.readuntil(b"\r"), self.timeout)
                w.close()
                try:
                    await w.wait_closed()
                except Exception:
                    pass
                return resp.decode(errors="ignore").strip()
            except Exception as e:
                last_exc = e
                await asyncio.sleep(0.4 * (attempt + 1))  # backoff breve
        raise last_exc or RuntimeError("Errore PJLink sconosciuto")

    async def power(self, on: bool) -> bool:
        # POWR 1/0
        resp = await self._send_cmd(f"POWR {1 if on else 0}")
        return "OK" in resp

    async def set_input(self, source: str) -> bool:
        code = self.input_map.get(source.upper())
        if not code:
            raise ValueError(f"Sorgente non valida: {source}")
        resp = await self._send_cmd(f"INPT {code}")
        return "OK" in resp

    async def get_power(self) -> Optional[int]:
        resp = await self._send_cmd("POWR ?")
        # risposta tipica: %1POWR=0|1|2|3 oppure ERRA/ERRA
        m = re.search(r"POWR=(\d)", resp)
        return int(m.group(1)) if m else None


