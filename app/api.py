from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
import os,yaml,subprocess
from datetime import datetime
from app.state import set_public_state,get_public_state
from app.drivers.pjlink import PJLinkClient
from app.drivers.shelly_http import ShellyHTTP, ShellyHTTP_script
from app.drivers.dsp408 import DSP408Client
from app import power_schedule

from fastapi import BackgroundTasks
import asyncio, logging
from .config import devices

log = logging.getLogger("api")
router = APIRouter()

CONFIG_DEV=os.environ.get('ROOMCTL_DEVICES','/opt/roomctl/config/devices.yaml')

def load_devices():
 try:
  with open(CONFIG_DEV,'r',encoding='utf-8') as f: return yaml.safe_load(f) or {}
 except FileNotFoundError:
  return {'projector':{
			'host':'192.168.1.220',
			'port':4352,
			'password':'1234',
			'nic_warmup_s':12,
			'pjlink_timeout_s':8,
			'pjlink_retries':4,
			'post_power_on_delay_s':2},
			
		  'dsp':{
			'host':'192.168.1.230',
			'port':4196,
			'addr':3,
            'input': {'0':True,'1':False,'2':False,'3':False},
            'output': {'0':True,'1':False,'2':False,'3':False, '4':True,'5':False,'6':False,'7':False}},
			
		  'shelly1':{
			'base':'http://192.168.1.10',
			'ch1':0,
			'ch2':1},
			
		  'shelly2':{
			'base':'http://192.168.1.11',
			'ch1':0,
			'ch2':1,
            'inverti_corsa':False}}

cfg=load_devices()
class TokenReq(BaseModel): token:str|None=None
class PowerReq(TokenReq): on:bool
class InputReq(TokenReq): source:str
class DspMuteReq(TokenReq): mute:bool
class DspVolMasterReq(TokenReq): db:float
class DspVolInputReq(TokenReq): ch:int; db:float
class DspStepReq(TokenReq):
    # esempio: bus="in_a", delta=-1 o +1
    bus: str
    delta: int
class DateTimeReq(TokenReq):
    # formato atteso: "2024-07-03T14:00" (HTML datetime-local)
    datetime: str
    
class DspUsedReq(TokenReq):
    # channel: 'in0'..'in3' oppure 'out0'..'out7'
    channel: str
    used: bool

class DspRecallReq(TokenReq):
    # esempio: "F00", "U01", ...
    preset: str

class PowerBody(BaseModel):
    on: bool

class ShellyInvertReq(TokenReq):
    inverti_corsa: bool


class PowerScheduleReq(BaseModel):
    on_time: str
    off_time: str
    days: list[str]
    enabled: bool = True

class SetDateTimeReq(BaseModel):
    datetime: datetime

@router.get("/power/schedule")
async def get_power_schedule():
    """Restituisce la pianificazione di accensione/spegnimento del Raspberry."""

    return power_schedule.load_power_schedule()


@router.post("/power/schedule")
async def set_power_schedule(body: PowerScheduleReq):
    """Aggiorna e salva la pianificazione di accensione/spegnimento."""

    try:
        saved = power_schedule.save_power_schedule(body.dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return saved


@router.post("/special/reboot_terminal")
async def api_reboot_terminal(background_tasks: BackgroundTasks):
    """
    Richiede il riavvio del terminale.

    ATTENZIONE: questo comando riavvia l'intera macchina.
    Viene eseguito in background per permettere alla risposta HTTP
    di essere inviata prima che il processo venga terminato dal reboot.
    """

    def do_reboot():
        # scegli la variante corretta in base alla tua configurazione
        # Variante A: il servizio gira come root
        # os.system("/sbin/reboot")

        # Variante B: il servizio gira come utente normale con sudo configurato
        os.system("sudo /sbin/reboot")

    # pianifica il reboot in background
    background_tasks.add_task(do_reboot)

    # rispondi subito, prima che il processo venga ucciso
    return {"status": "reboot requested"}

@router.post("/special/set_datetime")
async def api_set_datetime(body: SetDateTimeReq):
    """Aggiorna la data/ora del sistema e la salva sull'RTC."""

    dt = body.datetime
    dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

    try:
        subprocess.run(["sudo", "date", "-s", dt_str], check=True)
        #subprocess.run(["sudo", "hwclock", "--systohc"], check=True)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Impossibile impostare data/ora: {exc}",
        ) from exc

    return {"status": "datetime updated", "datetime": dt_str}


async def _wait_power_state(pj: PJLinkClient, desired: int, budget_s: int) -> bool:
    # desired: 1=ON, 0=STANDBY; molti Epson rispondono 2=cooling, 3=warm-up
    #power_state={0:'STANDBY',1:"ON",2:'COOLING',3:'WARM-UP',4:'Undefined'}
    end = asyncio.get_running_loop().time() + budget_s
    st=4
    while asyncio.get_running_loop().time() < end:
        try:
            st = await pj.get_power()
            if st == desired:
                stato=get_public_state(); stato['text']='Sistema pronto'; set_public_state(stato)
                return True,st
        except Exception:
            stato=get_public_state(); stato['text']='Errore accensione proiettore'; set_public_state(stato)
            pass
        await asyncio.sleep(1.5)
        stato=get_public_state(); stato['text']='Errore accensione proiettore'; set_public_state(stato)
    return False,st

async def _power_sequence(on: bool):
    # Config
    pconf = devices["projector"]
    nic_warmup = int(pconf.get("nic_warmup_s", 12))
    tout = float(pconf.get("pjlink_timeout_s", 8))
    retr = int(pconf.get("pjlink_retries", 4))
    ping_check = bool(pconf.get("pjlink_ping_check", False))

    pj = PJLinkClient(
        host=pconf["host"],
        port=pconf.get("port", 4352),
        password=pconf.get("password", "1234"),
        timeout=tout,
        retries=retr,
        ping_check=ping_check,
    )

    shelly_main = ShellyHTTP(base=devices["shelly1"]["base"])
    ch_main = devices["shelly1"]["ch1"]

    if on:

        # 1) mains ON
        try:
            ok = await shelly_main.set_relay(ch_main, True)
        except Exception as exc:
            stato=get_public_state(); stato['text']='Errore alimentazione proiettore'; set_public_state(stato)
            raise HTTPException(status_code=502, detail=f"Mancata accensione alimentazione principale: {exc}") from exc
        if not ok:
            raise HTTPException(status_code=502, detail="Shelly non ha confermato l'accensione")

        await asyncio.sleep(nic_warmup)

        # 3) POWER ON via PJLink
        try:
            _ = await pj.power(True)
            #stato=get_public_state(); stato['text']='Proiettore -> ON'; set_public_state(stato)
        except Exception as e:
            stato=get_public_state(); stato['text']='Errore accensione proiettore'; set_public_state(stato)
            raise HTTPException(status_code=502, detail=f"Comando PJLink power-on fallito: {e}") from e

    else:
        # POWER OFF
        try:
            okp = await pj.power(False)
            #log.info("PJLink POWER OFF: %s", okp)
        except Exception as e:
            log.exception("PJLink POWER OFF error: %s", e)
            raise HTTPException(status_code=502, detail=f"Comando PJLink power-off fallito: {e}") from e

        # attesa cooldown a STANDBY (spesso serve)
        off = await _wait_power_state(pj, desired=0, budget_s=90)
        #log.info("Projector OFF ready: %s", off)

        # opzionale: spegnere mains dopo cooldown
        # ok = await shelly_main.set_relay(ch_main, False)
        #log.info("Shelly mains OFF: %s", ok)


async def _power_sequence_background(on: bool):
    """Esegue la sequenza di accensione/spegnimento senza propagare eccezioni.

    Quando viene usato come task in background la risposta HTTP è già stata
    inviata: sollevare HTTPException qui genera un errore runtime. Per questo
    motivo intercettiamo gli errori e ci limitiamo a loggarli e aggiornare lo
    stato pubblico con un messaggio sintetico.
    """

    try:
        await _power_sequence(on)
    except HTTPException as exc:
        log.warning("Projector power background failed: %s", exc)
        stato = get_public_state(); stato['text'] = 'Errore alimentazione proiettore'; set_public_state(stato)
    except Exception as exc:  # pragma: no cover - salvaguardia generica
        log.exception("Unexpected projector power background error")
        stato = get_public_state(); stato['text'] = 'Errore alimentazione proiettore'; set_public_state(stato)

@router.post("/projector/power")
async def projector_power(body: PowerBody, background: BackgroundTasks):
    # NON blocchiamo la richiesta: lanciamo un task e rispondiamo 202
    background.add_task(_power_sequence_background, body.on)
    return {"accepted": True, "on": body.on}


@router.post('/projector/input')
async def projector_input(body:InputReq):
    pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None))
    try:
        ok=await pj.set_input(body.source)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Impossibile cambiare sorgente PJLink: {exc}") from exc
    if not ok:
        raise HTTPException(500,'PJLink input failed')
    st=get_public_state(); st['projector']['input']=body.source.upper(); set_public_state(st)
    return {'ok':True}

# ================== DSP408 ==================

@router.post("/dsp/mute")
async def dsp_mute(body: DspMuteReq):
    """
    Mute/unmute globale: usa DSP408Client.mute_all(on).
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    # mappe input/output usate per escludere canali dal MUTE ALL
    used_in = (cfg.get("dsp") or {}).get("input", {}) or {}
    used_out = (cfg.get("dsp") or {}).get("output", {}) or {}
    print("used in",used_in,"--used out ",used_out)
    try:
        await dsp.mute_all(body.mute, used_inputs=used_in, used_outputs=used_out)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DSP non raggiungibile per mute: {exc}") from exc
    #return {"ok": True, "mute": bool(body.mute)}
    return {"ok": True}

@router.post("/dsp/used")
async def dsp_used(body: DspUsedReq):
    """
    Aggiorna lo stato dei canali usati (toggle IN/OUT) e lo salva in devices.yaml.
    body.channel: es. 'in0'..'in3' oppure 'out0'..'out7'
    """
    cfg_local = load_devices()
    dsp_cfg = cfg_local.setdefault('dsp', {})
    in_map = dsp_cfg.setdefault('input', {})
    out_map = dsp_cfg.setdefault('output', {})

    ch = body.channel
    if ch.startswith('in'):
        idx = ch[2:]
        in_map[idx] = bool(body.used)
    elif ch.startswith('out'):
        idx = ch[3:]
        out_map[idx] = bool(body.used)
    else:
        raise HTTPException(status_code=400, detail="Canale non valido")

    # salva su devices.yaml
    with open(CONFIG_DEV, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg_local, f)

    # aggiorna la cfg globale in memoria
    global cfg
    cfg = cfg_local

    return {"ok": True, "channel": ch, "used": bool(body.used)}


@router.post("/dsp/gain")
async def dsp_gain(body: DspStepReq):
    """
    Step di gain (in dB) sul bus indicato (in_a, out0..3).
    delta viene usato solo come segno: >0 = +1 step, <0 = -1 step.
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    sign = 1 if body.delta >= 0 else -1
    try:
        new_val = await dsp.apply_gain_delta(body.bus, sign)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DSP non raggiungibile per gain: {exc}") from exc
    return {"ok": True, "bus": body.bus, "gain_db": new_val}


@router.post("/dsp/volume")
async def dsp_volume(body: DspStepReq):
    """
    Step di volume (in dB) sul bus indicato (in_a, out0..3).
    Anche qui usiamo solo il segno di delta.
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    sign = 1 if body.delta >= 0 else -1
    try:
        new_val = await dsp.apply_volume_delta(body.bus, sign)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DSP non raggiungibile per volume: {exc}") from exc
    return {"ok": True, "bus": body.bus, "volume_db": new_val}


@router.post("/dsp/recall")
async def dsp_recall(body: DspRecallReq):
    """
    Richiama un preset del DSP (es. F00, U01, U02, U03).
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    try:
        await dsp.recall(body.preset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DSP non raggiungibile per preset: {exc}") from exc
    return {"ok": True, "preset": body.preset}


@router.get("/dsp/state")
async def dsp_state():
    """
    Legge i livelli dal DSP e li restituisce già raggruppati per bus,
    in modo comodo per la template Jinja.
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    try:
        levels = await dsp.read_levels()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DSP non raggiungibile per lettura livelli: {exc}") from exc
    # levels è del tipo {"gain": {"in_a": -3, ...}, "volume": {...}}
    gain_map = levels.get("gain", {})
    vol_map = levels.get("volume", {})
    by_bus: dict = {}
    for bus in set(gain_map.keys()) | set(vol_map.keys()):
        by_bus[bus] = {
            "gain": gain_map.get(bus),
            "volume": vol_map.get(bus),
        }
    return by_bus



def _map_shelly(sid:str):
 if sid=='shelly1_ch1': return cfg['shelly1']['base'],cfg['shelly1']['ch1']
 if sid=='shelly1_ch2': return cfg['shelly1']['base'],cfg['shelly1']['ch2']
 if sid=='shelly2_ch1': return cfg['shelly2']['base'],2
 if sid=='shelly2_ch2': return cfg['shelly2']['base'],3
 raise HTTPException(404,'Unknown Shelly sid')


def _is_shelly2_inverted() -> bool:
    return bool((cfg.get('shelly2') or {}).get('inverti_corsa', False))


@router.post('/shelly/inverti_corsa')
async def shelly_invert(body: ShellyInvertReq):
 cfg_local = load_devices()
 shelly2_cfg = cfg_local.setdefault('shelly2', {})
 shelly2_cfg['inverti_corsa'] = bool(body.inverti_corsa)
 with open(CONFIG_DEV, 'w', encoding='utf-8') as f:
     yaml.safe_dump(cfg_local, f)
 global cfg
 cfg = cfg_local
 return {'ok': True, 'inverti_corsa': bool(body.inverti_corsa)}


@router.post('/shelly/{sid}/set')
async def shelly_set(sid:str,body:PowerReq):
 base,ch=_map_shelly(sid)
 inverti_corsa=_is_shelly2_inverted()

 if ch==2:
     sh=ShellyHTTP_script(base)
     try:
         ok=sh.shelly_pro2pm_cover(action='close', inverti_corsa=inverti_corsa)
     except Exception as exc:
         raise HTTPException(status_code=502, detail=f"Shelly cover giù non raggiungibile: {exc}") from exc
 elif ch==3:
     sh = ShellyHTTP_script(base)
     try:
         ok = sh.shelly_pro2pm_cover(action='open', inverti_corsa=inverti_corsa)
     except Exception as exc:
         raise HTTPException(status_code=502, detail=f"Shelly cover su non raggiungibile: {exc}") from exc
 else:
     sh=ShellyHTTP(base)
     try:
         ok=await sh.set_relay(ch,body.on)
     except Exception as exc:
         raise HTTPException(status_code=502, detail=f"Shelly non raggiungibile: {exc}") from exc
 if not ok: raise HTTPException(500,'Shelly set failed')
 return {'ok':True}

@router.post('/scene/avvio_semplice')
async def scene_avvio_semplice():
 #stato=get_public_state(); stato['text']='Avvio lezione semplice...'; set_public_state(stato)
 base,ch=_map_shelly('shelly1_ch2')
 sh=ShellyHTTP(base)
 try:
     ok = await sh.set_relay(ch,True)
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"Shelly DSP non raggiungibile: {exc}") from exc
 if not ok:
     raise HTTPException(status_code=502, detail="Accensione DSP non confermata")
 await asyncio.sleep(6)
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 try:
     await dsp.mute_all(False)
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"DSP non raggiungibile durante avvio semplice: {exc}") from exc
 #await dsp.set_master_db(-20.0)
 return {'ok':True}

@router.post('/scene/avvio_proiettore')
async def scene_avvio_proiettore(payload:dict|None=None):
 source=(payload or {}).get('source') or 'HDMI1'
 base,ch=_map_shelly('shelly1_ch2')
 sh=ShellyHTTP(base)
 try:
     ok = await sh.set_relay(ch,True)
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"Shelly DSP non raggiungibile: {exc}") from exc
 if not ok:
     raise HTTPException(status_code=502, detail="Accensione DSP non confermata")
 await asyncio.sleep(6)
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 try:
     await dsp.mute_all(False)
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"DSP non raggiungibile durante avvio proiettore: {exc}") from exc
 pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None))
 #base,ch=cfg['shelly2']['base'],cfg['shelly2']['ch1']
 sh=ShellyHTTP_script(cfg['shelly2']['base'])
 try:
     sh.shelly_pro2pm_cover(action='close', inverti_corsa=_is_shelly2_inverted())
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"Comando telo non riuscito: {exc}") from exc
 await _power_sequence(True)
 await asyncio.sleep(20)
 print("set source")
 try:
     await pj.set_input(source)
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"Cambio sorgente PJLink fallito: {exc}") from exc
 st=get_public_state(); st['projector']['power']=True; st['projector']['input']=source.upper(); set_public_state(st)
 return {'ok':True}

@router.post('/scene/spegni_aula')
async def scene_spegni_aula():
 #_=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None)) #crea istanza proiettore

 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))  #crea istanza DSP
 try:
     await dsp.mute_all(True)               #disabilita ingresso A e uscite 0-3
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"DSP non raggiungibile durante spegnimento: {exc}") from exc
 await asyncio.sleep(6)
 base,ch=cfg['shelly1']['base'],cfg['shelly1']['ch1']
 sh1 = ShellyHTTP(base)  # crea istanza shelly1
 try:
     await sh1.set_relay(cfg['shelly1']['ch2'], False)  # disattiva alimentazione per DSP
 except Exception as exc:
     raise HTTPException(status_code=502, detail=f"Shelly DSP non raggiungibile in spegnimento: {exc}") from exc
 #base,ch=cfg['shelly2']['base'],cfg['shelly2']['ch2']  #prende i parametri di Shelly2, 'telo su/giu'
 st = get_public_state()
 if st['current_lesson'] != 'semplice':
    await _power_sequence(False)                #avvia sequenza di off
    sh2 = ShellyHTTP_script(cfg['shelly2']['base'])
    try:
        sh2.shelly_pro2pm_cover(action='open', inverti_corsa=_is_shelly2_inverted())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Comando telo in apertura non riuscito: {exc}") from exc
    sh1=ShellyHTTP_script(base) #crea istanza shelly1
    try:
        sh1.projct_off_main()                       #disttiva alimentazione per proiettore
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Shelly principale non raggiungibile per spegnimento: {exc}") from exc
 st=get_public_state(); st['projector']['power']=False;st['text']="Lezione terminata..."; set_public_state(st)  #aggiorna stato
 return {'ok':True}


