from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
import os,yaml
from app.state import set_public_state,get_public_state
from app.drivers.pjlink import PJLinkClient
from app.drivers.shelly_http import ShellyHTTP
from app.drivers.dsp408 import DSP408Client

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
			'ch2':1}}

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
class DspRecallReq(TokenReq):
    # esempio: "F00", "U01", ...
    preset: str
class PowerBody(BaseModel):
    on: bool

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

    pj = PJLinkClient(
        host=pconf["host"],
        port=pconf.get("port", 4352),
        password=pconf.get("password", "1234"),
        timeout=tout,
        retries=retr,
    )

    shelly_main = ShellyHTTP(base=devices["shelly1"]["base"])
    ch_main = devices["shelly1"]["ch1"]

    if on:
        
        # 1) mains ON
        ok = await shelly_main.set_relay(ch_main, True)
        #log.info("Shelly mains ON: %s", ok)
        stato=get_public_state(); stato['text']='Alimentazione -> ON'; set_public_state(stato)

        # 2) attesa NIC
        stato=get_public_state(); stato['text']='Alimentazione --> ON'; set_public_state(stato)
        await asyncio.sleep(nic_warmup)

        # 3) POWER ON via PJLink
        try:
            okp = await pj.power(True)
            stato=get_public_state(); stato['text']='Proiettore -> ON'; set_public_state(stato)
        except Exception as e:
            stato=get_public_state(); stato['text']='Errore accensione proiettore'; set_public_state(stato)
            #log.exception("PJLink POWER ON error: %s", e)

        # 4) attesa stato ON (fino a 60 s per sicurezza)
        #await asyncio.sleep(30)
        #try:
        #    stato=get_public_state(); stato['text']='Warm-up proiettore...'; set_public_state(stato)
        #    ready,pw_st = await _wait_power_state(pj, desired=1, budget_s=120)
        #except Exception:
        #    stato=get_public_state(); stato['text']=f"Anomalia stato proiettore: {power_state[pw_st]}"; set_public_state(stato)
        #log.info("Projector ON ready: %s", ready)

    else:
        # POWER OFF
        try:
            okp = await pj.power(False)
            #log.info("PJLink POWER OFF: %s", okp)
        except Exception as e:
            log.exception("PJLink POWER OFF error: %s", e)

        # attesa cooldown a STANDBY (spesso serve)
        off = await _wait_power_state(pj, desired=0, budget_s=90)
        #log.info("Projector OFF ready: %s", off)

        # opzionale: spegnere mains dopo cooldown
        # ok = await shelly_main.set_relay(ch_main, False)
        #log.info("Shelly mains OFF: %s", ok)


async def _power_sequence_background(on: bool):
    """Esegue la sequenza di alimentazione in un task di background."""

    try:
        await _power_sequence(on)
    except HTTPException as exc:
        log.warning("Projector power background failed: %s", exc)
        stato = get_public_state(); stato['text'] = 'Errore alimentazione proiettore'; set_public_state(stato)
    except Exception:
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
 ok=await pj.set_input(body.source)
 if not ok: raise HTTPException(500,'PJLink input failed')
 st=get_public_state(); st['projector']['input']=body.source.upper(); set_public_state(st)
 return {'ok':True}

# ================== DSP408 ==================

@router.post("/dsp/mute")
async def dsp_mute(body: DspMuteReq):
    """
    Mute/unmute globale: usa DSP408Client.mute_all(on).
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    # il driver espone mute_all(on: bool)
    await dsp.mute_all(body.mute)
    #return {"ok": True, "mute": bool(body.mute)}
    return {"ok": True}

@router.post("/dsp/gain")
async def dsp_gain(body: DspStepReq):
    """
    Step di gain (in dB) sul bus indicato (in_a, out0..3).
    delta viene usato solo come segno: >0 = +1 step, <0 = -1 step.
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    sign = 1 if body.delta >= 0 else -1
    new_val = await dsp.apply_gain_delta(body.bus, sign)
    return {"ok": True, "bus": body.bus, "gain_db": new_val}


@router.post("/dsp/volume")
async def dsp_volume(body: DspStepReq):
    """
    Step di volume (in dB) sul bus indicato (in_a, out0..3).
    Anche qui usiamo solo il segno di delta.
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    sign = 1 if body.delta >= 0 else -1
    new_val = await dsp.apply_volume_delta(body.bus, sign)
    return {"ok": True, "bus": body.bus, "volume_db": new_val}


@router.post("/dsp/recall")
async def dsp_recall(body: DspRecallReq):
    """
    Richiama un preset del DSP (es. F00, U01, U02, U03).
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    await dsp.recall(body.preset)
    return {"ok": True, "preset": body.preset}


@router.get("/dsp/state")
async def dsp_state():
    """
    Legge i livelli dal DSP e li restituisce già raggruppati per bus,
    in modo comodo per la template Jinja.
    """
    dsp = DSP408Client(cfg["dsp"]["host"], int(cfg["dsp"]["port"]))
    levels = await dsp.read_levels()
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
 if sid=='shelly2_ch1': return cfg['shelly2']['base'],cfg['shelly2']['ch1']
 if sid=='shelly2_ch2': return cfg['shelly2']['base'],cfg['shelly2']['ch2']
 raise HTTPException(404,'Unknown Shelly sid')

@router.post('/shelly/{sid}/set')
async def shelly_set(sid:str,body:PowerReq):
 base,ch=_map_shelly(sid); sh=ShellyHTTP(base)
 ok=await sh.set_relay(ch,body.on)
 if not ok: raise HTTPException(500,'Shelly set failed')
 return {'ok':True}

@router.post('/scene/avvio_semplice')
async def scene_avvio_semplice():
 stato=get_public_state(); stato['text']='Avvio lezione semplice...'; set_public_state(stato)
 base,ch=_map_shelly('shelly1_ch2')
 sh=ShellyHTTP(base)
 await sh.set_relay(ch,True)
 await asyncio.sleep(6)
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 await dsp.mute_all(False)
 #await dsp.set_master_db(-20.0)
 return {'ok':True}

@router.post('/scene/avvio_proiettore')
async def scene_avvio_proiettore(payload:dict|None=None):
 source=(payload or {}).get('source') or 'HDMI1'
 base,ch=_map_shelly('shelly1_ch2')
 sh=ShellyHTTP(base)
 await sh.set_relay(ch,True)
 await asyncio.sleep(6)
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 await dsp.mute_all(False)
 pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None))
 base,ch=cfg['shelly2']['base'],cfg['shelly2']['ch1']
 sh=ShellyHTTP(base); await sh.pulse(ch,800)
 await _power_sequence(True)
 #ready,pw_st = await _wait_power_state(pj, desired=1, budget_s=120)
 await pj.set_input(source)
 st=get_public_state(); st['projector']['power']=True; st['projector']['input']=source.upper(); set_public_state(st)
 return {'ok':True}

@router.post('/scene/spegni_aula')
async def scene_spegni_aula():
 pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None)) #crea istanza proiettore
 await _power_sequence(False)		#avvia sequenza di off
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))  #crea istanza DSP
 await dsp.mute_all(True)		#disabilita ingresso A e uscite 0-3
 base,ch=cfg['shelly2']['base'],cfg['shelly2']['ch2']  #prende i parametri di Shelly2, 'telo su/giu'
 sh=ShellyHTTP(base); await sh.pulse(ch,800)		#crea istanza shelly2 e genera impulso per 'telo su'
 base1=cfg['shelly1']['base']; sh1=ShellyHTTP(base1) #crea istanza shelly1 
 await sh1.set_relay(cfg['shelly1']['ch1'],False)			#disttiva alimentazione per proiettore
 await sh1.set_relay(cfg['shelly1']['ch2'],False)			#disattiva alimentazione per DSP
 st=get_public_state(); st['projector']['power']=False;st['text']="Lezione terminata..."; set_public_state(st)  #aggiorna stato
 return {'ok':True}


