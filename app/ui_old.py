from fastapi import APIRouter,Request,Depends,Form
from fastapi.responses import HTMLResponse,RedirectResponse,JSONResponse
from fastapi.templating import Jinja2Templates
import httpx,os,asyncio,yaml
from .auth import require_operator,login_with_pin,logout,get_token_from_cookie
from .state import get_public_state
router=APIRouter(); templates=Jinja2Templates(directory='app/templates')
ROOMCTL_BASE=os.environ.get('ROOMCTL_BASE','http://127.0.0.1:8080')
UI_CONFIG=os.environ.get('ROOMCTL_UI_CONFIG','/opt/roomctl/config/ui.yaml')

def _load_ui():
 try:
  with open(UI_CONFIG,'r',encoding='utf-8') as f: return yaml.safe_load(f) or {}
 except FileNotFoundError: return {}
 except Exception: return {}

def _save_ui(d):
 os.makedirs(os.path.dirname(UI_CONFIG),exist_ok=True)
 with open(UI_CONFIG,'w',encoding='utf-8') as f: yaml.safe_dump(d,f)

def _get_show_combined()->bool: return bool(_load_ui().get('show_combined',True))

def _set_show_combined(v:bool): cfg=_load_ui(); cfg['show_combined']=bool(v); _save_ui(cfg)

async def _post(url,payload=None):
 async with httpx.AsyncClient(timeout=15) as c:
  r=await c.post(url,json=payload or {});
  try: body=r.json()
  except Exception: body=r.text
  return r.status_code,body

def _token(req:Request): return get_token_from_cookie(req)

@router.get('/',response_class=HTMLResponse)
async def home(req:Request):
 state=get_public_state(); return templates.TemplateResponse('index.html',{'request':req,'state':state,'show_combined':_get_show_combined()})

@router.post('/ui/scene/avvio_semplice')
async def ui_avvio_semplice():
 code,_=await _post(f"{ROOMCTL_BASE}/api/scene/avvio_semplice",{}); return JSONResponse({'ok':code==200},status_code=200 if code==200 else 500)

@router.post('/ui/scene/avvio_video')
async def ui_avvio_video():
 code,_=await _post(f"{ROOMCTL_BASE}/api/scene/avvio_proiettore",{}); return JSONResponse({'ok':code==200},status_code=200 if code==200 else 500)

@router.post('/ui/scene/avvio_video_combinata')
async def ui_avvio_video_combinata():
 code,_=await _post(f"{ROOMCTL_BASE}/api/scene/avvio_proiettore",{"source":"HDMI2"}); return JSONResponse({'ok':code==200},status_code=200 if code==200 else 500)

@router.post('/ui/scene/spegni_aula')
async def ui_spegni_aula():
 code,_=await _post(f"{ROOMCTL_BASE}/api/scene/spegni_aula",{}); return JSONResponse({'ok':code==200},status_code=200 if code==200 else 500)

@router.post('/auth/pin')
async def auth_pin(pin:str=Form(...)):
 t=await login_with_pin(pin)
 if not t: return RedirectResponse('/',status_code=303)
 resp=RedirectResponse('/operator',status_code=303); resp.set_cookie('rtoken',t,httponly=True,samesite='lax'); return resp

@router.get('/operator',response_class=HTMLResponse)
async def operator_get(req:Request,_=Depends(require_operator)):
 state=get_public_state(); return templates.TemplateResponse('operator.html',{'request':req,'state':state,'show_combined':_get_show_combined()})

@router.post('/auth/logout')
async def auth_logout(req:Request):
 resp=RedirectResponse('/',status_code=303); logout(resp); return resp

@router.post('/operator/toggle_combined')
async def op_toggle_combined(value:bool=Form(...),_=Depends(require_operator)):
 _set_show_combined(value); return RedirectResponse('/operator',status_code=303)

@router.post('/operator/projector/power')
async def op_proj_power(on:bool=Form(...),_=Depends(require_operator)):
 await _post(f"{ROOMCTL_BASE}/api/projector/power",{'on':bool(on)}); return RedirectResponse('/operator',status_code=303)

@router.post('/operator/projector/input')
async def op_proj_input(source:str=Form(...),_=Depends(require_operator)):
 await _post(f"{ROOMCTL_BASE}/api/projector/input",{'source':source}); return RedirectResponse('/operator',status_code=303)

@router.post('/operator/dsp/mute')
async def op_dsp_mute(mute:bool=Form(...),_=Depends(require_operator)):
 await _post(f"{ROOMCTL_BASE}/api/dsp/mute",{'mute':bool(mute)}); return RedirectResponse('/operator',status_code=303)

@router.post('/operator/dsp/vol_master')
async def op_dsp_vol_master(db:float=Form(...),_=Depends(require_operator)):
 await _post(f"{ROOMCTL_BASE}/api/dsp/vol_master",{'db':float(db)}); return RedirectResponse('/operator',status_code=303)

@router.post('/operator/dsp/vol_input')
async def op_dsp_vol_input(ch:int=Form(...),db:float=Form(...),_=Depends(require_operator)):
 await _post(f"{ROOMCTL_BASE}/api/dsp/vol_input",{'ch':int(ch),'db':float(db)}); return RedirectResponse('/operator',status_code=303)

@router.post('/operator/shelly/set')
async def op_shelly_set(sid:str=Form(...),on:bool=Form(...),_=Depends(require_operator)):
 await _post(f"{ROOMCTL_BASE}/api/shelly/{sid}/set",{'on':bool(on)}); return RedirectResponse('/operator',status_code=303)

@router.post('/operator/shelly/pulse')
async def op_shelly_pulse(sid:str=Form(...),_=Depends(require_operator)):
 await _post(f"{ROOMCTL_BASE}/api/shelly/{sid}/set",{'on':True}); import asyncio; await asyncio.sleep(0.8); await _post(f"{ROOMCTL_BASE}/api/shelly/{sid}/set",{'on':False}); return RedirectResponse('/operator',status_code=303)
