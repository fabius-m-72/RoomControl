from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
import os,yaml
from app.state import set_public_state,get_public_state
from app.drivers.pjlink import PJLinkClient
from app.drivers.shelly_http import ShellyHTTP
from app.drivers.dsp408 import DSP408Client

CONFIG_DEV=os.environ.get('ROOMCTL_DEVICES','/opt/roomctl/config/devices.yaml')

def load_devices():
 default_cfg = {'projector':{'host':'192.168.1.20','password':''},'dsp':{'host':'192.168.1.40','port':8899},'shelly1':{'base':'http://192.168.1.30','ch1':1,'ch2':2},'shelly2':{'base':'http://192.168.1.31','ch1':1,'ch2':2}}
 try:
  with open(CONFIG_DEV,'r',encoding='utf-8') as f: return yaml.safe_load(f) or {}
 except FileNotFoundError:
  return dict(default_cfg)
 except (OSError, yaml.YAMLError) as exc:
  raise HTTPException(status_code=500, detail=f"Impossibile leggere devices.yaml: {exc}")

cfg=load_devices(); router=APIRouter()
class TokenReq(BaseModel): token:str|None=None
class PowerReq(TokenReq): on:bool
class InputReq(TokenReq): source:str
class DspMuteReq(TokenReq): mute:bool
class DspVolMasterReq(TokenReq): db:float
class DspVolInputReq(TokenReq): ch:int; db:float

@router.post('/projector/power')
async def projector_power(body:PowerReq):
 pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None))
 ok=await pj.power(body.on)
 if not ok: raise HTTPException(500,'PJLink power failed')
 st=get_public_state(); st['projector']['power']=bool(body.on); set_public_state(st)
 return {'ok':True}

@router.post('/projector/input')
async def projector_input(body:InputReq):
 pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None))
 ok=await pj.set_input(body.source)
 if not ok: raise HTTPException(500,'PJLink input failed')
 st=get_public_state(); st['projector']['input']=body.source.upper(); set_public_state(st)
 return {'ok':True}

@router.post('/dsp/mute')
async def dsp_mute(body:DspMuteReq):
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 ok=await dsp.mute(body.mute)
 if not ok: raise HTTPException(500,'DSP mute failed')
 return {'ok':True}

@router.post('/dsp/vol_master')
async def dsp_vol_master(body:DspVolMasterReq):
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 ok=await dsp.set_master_db(body.db)
 if not ok: raise HTTPException(500,'DSP vol master failed')
 return {'ok':True}

@router.post('/dsp/vol_input')
async def dsp_vol_input(body:DspVolInputReq):
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 ok=await dsp.set_input_db(body.ch,body.db)
 if not ok: raise HTTPException(500,'DSP vol input failed')
 return {'ok':True}


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
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 await dsp.mute(False); await dsp.set_master_db(-20.0)
 return {'ok':True}

@router.post('/scene/avvio_proiettore')
async def scene_avvio_proiettore(payload:dict|None=None):
 source=(payload or {}).get('source') or 'HDMI1'
 pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None))
 await pj.power(True); await pj.set_input(source)
 base,ch=cfg['shelly2']['base'],cfg['shelly2']['ch1']
 sh=ShellyHTTP(base); await sh.pulse(ch,800)
 st=get_public_state(); st['projector']['power']=True; st['projector']['input']=source.upper(); set_public_state(st)
 return {'ok':True}

@router.post('/scene/spegni_aula')
async def scene_spegni_aula():
 pj=PJLinkClient(cfg['projector']['host'],password=(cfg['projector'].get('password') or None))
 await pj.power(False)
 dsp=DSP408Client(cfg['dsp']['host'],int(cfg['dsp']['port']))
 await dsp.mute(True)
 base,ch=cfg['shelly2']['base'],cfg['shelly2']['ch2']
 sh=ShellyHTTP(base); await sh.pulse(ch,800)
 base1=cfg['shelly1']['base']; sh1=ShellyHTTP(base1)
 await sh1.set_relay(cfg['shelly1']['ch1'],False)
 await sh1.set_relay(cfg['shelly1']['ch2'],False)
 st=get_public_state(); st['projector']['power']=False; set_public_state(st)
 return {'ok':True}
