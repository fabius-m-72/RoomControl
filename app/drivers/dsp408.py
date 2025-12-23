# app/drivers/dsp408.py
from __future__ import annotations
import asyncio
import socket
import time
from typing import Tuple, Optional, Union, Dict

# === Stack di basso livello (TUO codice, con minimi ritocchi) ===

DLE = 0x7B  # first byte
STX = 0x7D  # second byte

def code_to_db(code: int) -> float:
	if not (0 <= code <= 400):
		raise ValueError("code fuori range [0..400]")
	if code <= 80:
		return -60.0 + code * 0.5
	if code <= 280:
		return -20.0 + (code - 80) * 0.1
	return 0.0 + (code - 280) * 0.1

def db_to_code(db: float) -> int:
	if db < -60.0: db = -60.0
	if db > +12.0: db = +12.0
	if db <= -20.0:
		return int(round((db + 60.0) / 0.5))
	if db <= 0.0:
		return int(round(80 + (db + 20.0) / 0.1))
	return int(round(280 + (db - 0.0) / 0.1))

class RS232TCPClient:
	def __init__(self, host: str, port: int, device_address: int = 3, min_step_ms: int = 20, timeout: float = 2.0, debug: bool=False):
		if not (1 <= device_address <= 254):
			raise ValueError("device_address deve essere 1..254")
		self.host = host
		self.port = port
		self.addr = device_address
		self.min_step = max(20, int(min_step_ms)) / 1000.0
		self.timeout = timeout
		self._sock: Optional[socket.socket] = None
		self._last_send_ts = 0.0
		self.debug = debug

	def connect(self):
		if self._sock: return
		s = socket.create_connection((self.host, self.port), timeout=self.timeout)
		s.settimeout(self.timeout)
		self._sock = s

	def close(self):
			if self._sock:
					try: self._sock.close()
					finally: self._sock = None

	def check_connection(self) -> bool:
			"""Tenta una lettura rapida per verificare se il DSP è raggiungibile."""

			try:
					self.connect()
					resp = self.send_command(
							self.CMD_GET_PRESET,
							0x00,
							0x00,
							0x00,
							answ_byte=1,
							expect_reply=True,
							expect_echo=False,
							reply_timeout=2.0,
					)
					return resp is not None
			except Exception:
					return False

	@staticmethod
	def _build_packet(addr: int, cmd: int, d1: int, d2: int, d3: int) -> bytes:
		return bytes([DLE, STX, addr & 0xFF, cmd & 0xFF, d1 & 0xFF, d2 & 0xFF, d3 & 0xFF, STX, DLE])

	@staticmethod
	def _parse_packet(pkt: bytes) -> Tuple[int, int]:
		# semplificata per la tua versione "ridotta"
		if len(pkt) == 1:
			return pkt[0], 0
		elif len(pkt) == 2:
			return pkt[0], pkt[1]
		else:
			# fallback innocuo
			return pkt[0], pkt[1] if len(pkt) > 1 else 0

	def _recv_exact(self, n: int, deadline: Optional[float] = None) -> bytes:
		if not self._sock:
			raise RuntimeError("Socket non connesso")
		data = bytearray()
		while len(data) < n:
			if deadline is not None:
				remaining = deadline - time.monotonic()
				if remaining <= 0:
					raise TimeoutError("timeout in ricezione (deadline)")
				self._sock.settimeout(min(self.timeout, remaining))
			chunk = self._sock.recv(n - len(data))
			if not chunk:
				raise ConnectionError("Connessione chiusa dal peer")
			data.extend(chunk)
		if self.debug: print(f"RX: {bytes(data)}")
		return bytes(data)

	CMDS_WITH_REPLY = {0x48, 0x49, 0x4A}

	def send_command(self, cmd: int, d1: int=0, d2: int=0, d3: int=0, answ_byte=1, *,
					 expect_reply: Optional[bool]=None, expect_echo: bool=False,
					 reply_timeout: Optional[float]=None) -> Union[Tuple[int, int], None]:
		self.connect()
		now = time.monotonic()
		delta = now - self._last_send_ts
		if delta < self.min_step:
			time.sleep(self.min_step - delta)
		pkt = self._build_packet(self.addr, cmd, d1, d2, d3)
		if self.debug: print(f"TX: {pkt}")
		self._sock.sendall(pkt)
		self._last_send_ts = time.monotonic()
		if expect_reply is None:
			expect_reply = (cmd in self.CMDS_WITH_REPLY)
		if not expect_reply:
			return None
		time.sleep(0.02)
		total_timeout = reply_timeout if reply_timeout is not None else max(self.timeout, 2.0)
		resp = self._read_reply_packet(sent_pkt=pkt, allow_echo=expect_echo, n_byte=answ_byte, total_timeout=total_timeout)
		r_d2, r_d3 = self._parse_packet(resp)
		return r_d2, r_d3

	CMD_GAIN          = 0x41
	CMD_MUTE          = 0x42
	CMD_LOAD_PRESET   = 0x43
	CMD_INPUT_VOLUME  = 0x44
	CMD_OUTPUT_VOLUME = 0x45
	CMD_GET_GAIN      = 0x48
	CMD_GET_MUTE      = 0x49
	CMD_GET_PRESET    = 0x4A

	def _read_reply_packet(self, *, sent_pkt: bytes, allow_echo: bool, n_byte: int, total_timeout: float) -> bytes:
		deadline = time.monotonic() + total_timeout
		first = self._recv_exact(n_byte, deadline)
		if allow_echo and first == sent_pkt:
			second = self._recv_exact(n_byte, deadline)
			return second
		return first

	def set_gain(self, is_output: bool, channel: int, sign: int) -> None:
		d1 = 1 if is_output else 0
		d2 = int(channel) & 0xFF
		d3 = 1 if sign else 0   #  False: 0 > +1   True: 1 > -1
		self.send_command(self.CMD_GAIN, d1, d2, d3, expect_reply=False,expect_echo=False)

	def set_mute(self, *, is_output: bool, channel: int, mute: bool) -> None:
		d1 = 1 if is_output else 0
		d2 = int(channel) & 0xFF
		d3 = 1 if mute else 0
		self.send_command(self.CMD_MUTE, d1, d2, d3, expect_reply=False)

	def get_mute(self, *, is_output: bool, channel: int) -> bool:
		d1 = 1 if is_output else 0
		d2 = int(channel) & 0xFF
		r = self.send_command(self.CMD_GET_MUTE, d1, d2, 0x00, answ_byte=1, expect_reply=True, expect_echo=False, reply_timeout=2.0)
		# ritorno singolo byte; True se !=0
		if r is None: return False
		r_d2, _ = r
		return bool(r_d2)

	def recall_preset(self, *, user: bool, preset_index: int) -> None:
		d1 = 1 if user else 0
		d2 = int(preset_index) & 0xFF
		self.send_command(self.CMD_LOAD_PRESET, d1, d2, 0x00, expect_reply=False, expect_echo=False)

	def get_preset(self) -> int:
		r = self.send_command(self.CMD_GET_PRESET, 0x00, 0x00, 0x00, answ_byte=1, expect_reply=True, expect_echo=False, reply_timeout=2.0)
		return r[0] if r else 0

	def set_input_volume_db(self, channel: int, db: float) -> None:
		code = db_to_code(db)
		hi = (code >> 8) & 0xFF; lo = code & 0xFF
		self.send_command(self.CMD_INPUT_VOLUME, channel & 0xFF, hi, lo, expect_reply=False, expect_echo=False)

	def set_output_volume_db(self, channel: int, db: float) -> None:
		code = db_to_code(db)
		print('code:',code)
		hi = (code >> 8) & 0xFF; lo = code & 0xFF
		self.send_command(self.CMD_OUTPUT_VOLUME, channel & 0xFF, hi, lo, expect_reply=False, expect_echo=False)

	def get_gain_db(self, *, is_output: bool, channel: int) -> float:
		d1 = 1 if is_output else 0
		ch = channel & 0xFF
		try:
			r_d2, r_d3 = self.send_command(self.CMD_GET_GAIN, d1, ch, 0x00, answ_byte=2, expect_reply=True, expect_echo=False, reply_timeout=3.0)
		except TimeoutError:
			r_d2, r_d3 = self.send_command(self.CMD_GET_GAIN, d1, ch, 0x00, answ_byte=2, expect_reply=True, expect_echo=False, reply_timeout=3.0)
		if r_d2 == 0 and r_d3 == 0:
			return 0.0
		code = (r_d2 << 8) | r_d3
		if code > 400 and r_d2 == 0:
			code = r_d3
		code = max(0, min(400, code))
		print(code)
		return code_to_db(code)

# === Wrapper Async “alto livello” ===

class DSP408Client:
	"""
	Facciata async per FastAPI. Usa RS232TCPClient (sincrono) in thread.
	"""
	def __init__(self, host: str, port: int = 4196, timeout: float = 3.0, addr: int = 3, min_step_ms: int = 50,
				 bus_map: Optional[Dict[str, Tuple[bool,int]]] = None, debug: bool=False):
		self._cli = RS232TCPClient(host=host, port=port, device_address=addr, min_step_ms=min_step_ms, timeout=timeout, debug=debug)
		# bus_map: 'in_a' -> (False, 0), 'out0' -> (True, 0) ...
		self.bus_map = bus_map or {
			"in_a": (False, 0),  # input A = canale 0
			"in_b": (False, 1),  # input A = canale 0
			"in_c": (False, 2),  # input A = canale 0
			"in_d": (False, 3),  # input A = canale 0
			"out0": (True, 0),
			"out1": (True, 1),
			"out2": (True, 2),
			"out3": (True, 3),
			"out4": (True, 4),
			"out5": (True, 5),
			"out6": (True, 6),
			"out7": (True, 7),}

	def _resolve(self, bus: str) -> Tuple[bool, int]:
		if bus not in self.bus_map:
			raise ValueError(f"bus sconosciuto: {bus}")
		return self.bus_map[bus]


	async def mute_all(self, on: bool, used_inputs: Optional[Dict[str, bool]] = None,
					   used_outputs: Optional[Dict[str, bool]] = None) -> None:
		def _do():
			in_map = used_inputs or {}
			out_map = used_outputs or {}
			print(in_map)
			print("--",out_map)

			def _as_bool(val: object, default: bool = True) -> bool:
				if val is None:
					return default
				if isinstance(val, str):
					return val.strip().lower() in ("true", "1", "on", "yes")
				return bool(val)

			# IN 0..3 e OUT 0..3
			for ch in (0, 1, 2, 3):
				ch_s = str(ch)
				mute_in = bool(on) and (_as_bool(in_map.get(ch_s, True)) or bool(on))
				print("mute-in(",ch_s,")=",mute_in)
				self._cli.set_mute(is_output=False, channel=ch, mute=mute_in)
				mute_out = bool(on) and (_as_bool(out_map.get(ch_s, True)) or bool(on))
				self._cli.set_mute(is_output=True, channel=ch, mute=mute_out)

			# OUT 4..7
			for ch in (4, 5, 6, 7):
				ch_s = str(ch)
				mute_out = bool(on) and (_as_bool(out_map.get(ch_s, True)) or bool(on))
				self._cli.set_mute(is_output=True, channel=ch, mute=mute_out)

		await asyncio.to_thread(_do)

	async def apply_gain_delta(self, bus: str, sign: int) -> float:
		"""
		Aumenta/diminuisce di 1 dB (o 0.5/0.1 a seconda della tabella reale).
		Qui uso step = 1.0 dB come “delta logico”.
		"""
		is_out, ch = self._resolve(bus)
		print(bus,is_out,ch)
		def _do() -> float:
			cur = self._cli.get_gain_db(is_output=is_out, channel=ch)
			print("curr",cur)
			new = max(-60.0, min(+12.0, cur + (1.0 if sign > 0 else -1.0)))
			print("new ",new)
			self._cli.set_gain(is_out,ch, sign)
			return new
		return await asyncio.to_thread(_do)

	async def apply_volume_delta(self, bus: str, sign: int) -> float:
		def _do() -> float:
			#cur = self._cli.get_gain_db(is_output=is_out, channel=ch)
			#new = max(-60.0, min(+12.0, cur + (1.0 if sign > 0 else -1.0)))
			#print(cur,new)
			if is_out:
				self._cli.set_output_volume_db(ch, new)
			else:
				# IN A ↔ set_input_volume_db
				self._cli.set_input_volume_db(ch, new)
			return new
		# Se “volume” nel tuo DSP è lo stesso valore del gain, riuso la stessa logica.
		return await self.apply_gain_delta(bus, sign)

	async def recall(self, preset: str) -> None:
		"""
		preset: 'F00' (factory) o 'U01'..'U03' (user).
		"""
		def _do():
			if preset == "F00":
				self._cli.recall_preset(user=False, preset_index=0)
			else:
				idx = int(preset[1:])  # U01 -> 1
				self._cli.recall_preset(user=True, preset_index=idx)
		await asyncio.to_thread(_do)

	async def check_status(self) -> bool:
			"""Controlla rapidamente se il DSP è online."""

			return await asyncio.to_thread(self._cli.check_connection)

	async def read_levels(self) -> Dict[str, Dict[str, float]]:
			def _do() -> Dict[str, Dict[str, float]]:
					g: Dict[str,float] = {}
					v: Dict[str,float] = {}
					for bus, (is_out, ch) in self.bus_map.items():
						val = self._cli.get_gain_db(is_output=is_out, channel=ch)
						g[bus] = val
						v[bus] = val
					return {"gain": g, "volume": v}
			return await asyncio.to_thread(_do)



