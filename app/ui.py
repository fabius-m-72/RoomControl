from datetime import datetime
import subprocess
from pathlib import Path
from fastapi import APIRouter,Request,Depends,Form,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse,JSONResponse
from fastapi.templating import Jinja2Templates
import httpx,os,asyncio,yaml
from .auth import require_operator,login_with_pin,logout,get_token_from_cookie
from .state import get_public_state,set_public_state
from .config import devices
router=APIRouter(); templates=Jinja2Templates(directory='app/templates')
ROOMCTL_BASE=os.environ.get('ROOMCTL_BASE','http://127.0.0.1:8080')
UI_CONFIG=os.environ.get('ROOMCTL_UI_CONFIG','/opt/roomctl/config/ui.yaml')
CONFIG_DEV=os.environ.get('ROOMCTL_DEVICES','/opt/roomctl/config/devices.yaml')


def _load_ui():
 try:
  with open(UI_CONFIG,'r',encoding='utf-8') as f: return yaml.safe_load(f) or {}
 except FileNotFoundError: return {}
 except Exception: return {}

def _save_ui(d):
 try:
  os.makedirs(os.path.dirname(UI_CONFIG),exist_ok=True)
  with open(UI_CONFIG,'w',encoding='utf-8') as f:
   yaml.safe_dump(d,f)
 except Exception as exc:
  raise HTTPException(status_code=500, detail=f"Impossibile salvare la configurazione UI: {exc}")

def _get_show_combined()->bool: return bool(_load_ui().get('show_combined',True))

def _set_show_combined(v:bool): cfg=_load_ui(); cfg['show_combined']=bool(v); _save_ui(cfg)

def _load_devices_cfg():
 try:
  with open(CONFIG_DEV,'r',encoding='utf-8') as f:
   return yaml.safe_load(f) or {}
 except FileNotFoundError:
  return {}
 except Exception:
  return {}

def _get_dsp_used()->dict:
 cfg=_load_devices_cfg()
 dsp=cfg.get('dsp',{})
 in_map=dsp.get('input',{}) or {}
 out_map=dsp.get('output',{}) or {}
 used={}
 for i in range(4):
  used[f"in{i}"]=bool(in_map.get(str(i),True))
 for i in range(8):
  used[f"out{i}"]=bool(out_map.get(str(i),True))
 return used
 
def _get_shelly_invert()->bool:
 cfg=_load_devices_cfg()
 return bool((cfg.get('shelly2') or {}).get('inverti_corsa', False))

def _read_rtc_vbat() -> float | None:
    """Legge la tensione batteria dell'RTC da sysfs, se disponibile.

    Ritorna il valore in Volt oppure ``None`` se non rilevabile.
	
    candidates = [
        Path("/sys/class/power_supply/rtc-battery/voltage_now"),
        Path("/sys/class/power_supply/rtc-vbat/voltage_now"),
        Path("/sys/class/power_supply/rtc/voltage_now"),
    ]

    for path in candidates:
        try:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                continue

            value = float(raw)
            # I driver di alimentazione riportano spesso microvolt/millivolt
            if value > 10000:
                value /= 1_000_000
            elif value > 100:
                value /= 1000
            return value
        except Exception:
            continue

    return None """
	
    output=subprocess.check_output("vcgencmd pmic_read_adc BATT_V", shell=True,text=True)
    val_str=output.split('=')[1].replace('V','').strip()
    val=float(val_str)
    return round(val,3)


def _set_device_error(device_name: str, exc: Exception | None = None):
    """Aggiorna lo stato pubblico con un messaggio di errore per il footer.

    Il testo include il nome del dispositivo che non risponde e, se presente,
    il dettaglio dell'eccezione ricevuta.
    """

    state = get_public_state()
    detail = ""

    if isinstance(exc, HTTPException):
        detail = f" ({exc.detail})"
    elif exc:
        detail = f" ({exc})"

    state["text"] = f"{device_name} non risponde{detail}".strip()
    set_public_state(state)


async def _post(url: str, payload: dict | None = None):
  timeout = httpx.Timeout(connect=8.0, read=120.0, write=10.0, pool=8.0)
  try:
        async with httpx.AsyncClient(timeout=timeout) as c:
          r = await c.post(url, json=payload or {})
  except httpx.TimeoutException:
		#qui decidi cosa fare
        raise HTTPException(status_code=504, detail=f"Timeout parlando con {url}")
  except httpx.RequestError as e:
        raise HTTPException(status_code=504, detail=f"Timeout di rete verso {url}: {e}")
		# Se il backend accetta e prosegue in background → 202
  if r.status_code >= 500:
        raise HTTPException(status_code=502, detail=f"Backend error {r.status_code}	da {url}")
  if "application/json" in r.headers.get("content-type",""):
        return r.json()
  return r.text
		
async def _get(url: str) -> dict:
    # stesso timeout "rilassato" di _post
    timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()

def _token(req:Request): return get_token_from_cookie(req)

def _set_state_text(message: str) -> dict:
    state = get_public_state()
    state["text"] = message
    set_public_state(state)
    return state

@router.get('/', response_class=HTMLResponse)
async def home(req: Request, pin_error: bool = False):
    state = get_public_state()
    return templates.TemplateResponse(
        'index.html',
        {
            'request': req,
            'state': state,
            'show_combined': _get_show_combined(),
            'pin_error': pin_error,
        }
    )

@router.post('/ui/scene/avvio_semplice')
async def ui_avvio_semplice():
    state = _set_state_text("Avvio lezione semplice in corso…")
    try:
        _ = await _post(f"{ROOMCTL_BASE}/api/scene/avvio_semplice", {})
    except HTTPException as exc:
        _set_state_text(f"Errore avvio lezione semplice: {exc.detail}")
        return RedirectResponse(url="/", status_code=303)

    state["text"] = "Avviata lezione solo audio"
    state["current_lesson"] = "semplice"
    set_public_state(state)
    return RedirectResponse(url="/", status_code=303)


@router.post('/ui/scene/avvio_video')
async def ui_avvio_video():
    state = _set_state_text("Avvio lezione video in corso…")
    try:
        _ = await _post(f"{ROOMCTL_BASE}/api/scene/avvio_proiettore", {})
    except HTTPException as exc:
        _set_state_text(f"Errore avvio lezione video: {exc.detail}")
        return RedirectResponse(url="/", status_code=303)

    state["text"] = "Lezione video avviata"
    state["current_lesson"] = "video"
    set_public_state(state)
    return RedirectResponse(url="/", status_code=303)


@router.post('/ui/scene/avvio_video_combinata')
async def ui_avvio_video_combinata():
    state = _set_state_text("Avvio lezione video combinata in corso…")
    try:
        _ = await _post(
            f"{ROOMCTL_BASE}/api/scene/avvio_proiettore",
            {"source": "HDMI2"},
        )
    except HTTPException as exc:
        _set_state_text(f"Errore avvio lezione combinata: {exc.detail}")
        return RedirectResponse(url="/", status_code=303)

    state["text"] = "Lezione video combinata avviata"
    state["current_lesson"] = "combinata"
    set_public_state(state)
    return RedirectResponse(url="/", status_code=303)

   
@router.post('/ui/scene/spegni_aula')
async def ui_spegni_aula():
    state = _set_state_text("Arresto lezione e spegnimento aula in corso…")
    try:
        _ = await _post(f"{ROOMCTL_BASE}/api/scene/spegni_aula", {})
    except HTTPException as exc:
        _set_state_text(f"Errore spegnimento aula: {exc.detail}")
        return RedirectResponse(url="/", status_code=303)

    state["text"] = "Aula spenta: sistema pronto"
    # nessuna lezione attiva
    state["current_lesson"] = None #state.pop("current_lesson", None) per rimuovere proprio la chiave
    set_public_state(state)
    return RedirectResponse(url="/", status_code=303)


@router.post('/auth/pin')
async def auth_pin(pin: str = Form(...)):
    t = await login_with_pin(pin)
    if not t:
        # PIN errato: torna alla home con il flag pin_error
        return RedirectResponse('/?pin_error=1', status_code=303)
    # PIN corretto: imposta il cookie e vai in area operatore
    resp = RedirectResponse('/operator', status_code=303)
    resp.set_cookie('rtoken', t, httponly=True, samesite='lax')
    return resp

@router.get("/operator", response_class=HTMLResponse)
async def operator_get(req: Request, _=Depends(require_operator)):
    state = get_public_state()
    rtc_vbat = _read_rtc_vbat()


    # di default nessun dato DSP (per gestire eventuali errori)
    dsp_levels = None
    try:
        dsp_levels = await _get(f"{ROOMCTL_BASE}/api/dsp/state")
    except Exception:
        # se il DSP è spento / non raggiungibile, semplicemente
        # lasciamo dsp_levels = None e il template mostrerà "—"
        dsp_levels = None

    # stato dei toggle DSP (input/output) letto da devices.yaml
    try:
        dsp_used = _get_dsp_used()
    except Exception:
        dsp_used = None

    try:
        power_schedule = await _get(f"{ROOMCTL_BASE}/api/power/schedule")
    except Exception:
        power_schedule = None

    return templates.TemplateResponse(
        "operator.html",
        {
            "request": req,
            "state": state,
            "show_combined": _get_show_combined(),
            "dsp_levels": dsp_levels,
            "dsp_used": dsp_used,
            "shelly2_invert": _get_shelly_invert(),
            "power_schedule": power_schedule,
            "rtc_vbat": rtc_vbat,

        },
    )


@router.post('/auth/logout')
async def auth_logout(req:Request):
 resp=RedirectResponse('/',status_code=303); logout(resp); return resp

@router.post('/operator/toggle_combined')
async def op_toggle_combined(value:bool=Form(...),_=Depends(require_operator)):
    try:
        _set_show_combined(value)
    except Exception as exc:
        _set_device_error("Configurazione UI", exc)
    return RedirectResponse('/operator',status_code=303)

@router.post('/operator/projector/power')
async def op_proj_power(on:bool=Form(...),_=Depends(require_operator)):
    try:
        await _post(f"{ROOMCTL_BASE}/api/projector/power",{'on':bool(on)})
    except Exception as exc:
        _set_device_error("Proiettore", exc)
    return RedirectResponse('/operator',status_code=303)

@router.post('/operator/projector/input')
async def op_proj_input(source:str=Form(...),_=Depends(require_operator)):
    try:
        await _post(f"{ROOMCTL_BASE}/api/projector/input",{'source':source})
    except Exception as exc:
        _set_device_error("Proiettore", exc)
    return RedirectResponse('/operator',status_code=303)

@router.post("/operator/dsp/mute_all")
async def op_dsp_mute_all( on: bool = Form(...), _=Depends(require_operator),):
    # operator.html usa name="on", ma l'API vuole "mute"
    try:
        await _post(f"{ROOMCTL_BASE}/api/dsp/mute", {"mute": bool(on)})
    except Exception as exc:
        _set_device_error("DSP", exc)
    return RedirectResponse("/operator", status_code=303)

@router.post("/operator/dsp/used")
async def op_dsp_used(req: Request, _=Depends(require_operator)):
    """
    Gestisce i toggle IN/OUT del DSP (usano name="in0".."in3" / "out0".."out7").
    Legge il primo campo presente nel form e lo inoltra all'API /api/dsp/used.
    """
    form = await req.form()
    channel = None
    new_val = None
    for k, v in form.items():
        if k.startswith("in") or k.startswith("out"):
            channel = k
            new_val = v
            break

    if channel is not None and new_val is not None:
        used = str(new_val).lower() in ("true", "1", "on", "yes")
        try:
            await _post(
                f"{ROOMCTL_BASE}/api/dsp/used",
                {"channel": channel, "used": used},
            )
        except Exception as exc:
            _set_device_error("DSP", exc)

    return RedirectResponse("/operator", status_code=303)


@router.post("/operator/dsp/gain")
async def op_dsp_gain(
    bus: str = Form(...),
    delta: int = Form(...),
    _=Depends(require_operator),
):
    """
    Handler per i pulsanti GAIN +/- (bus = in_a, out0..3).
    """
    try:
        await _post(
            f"{ROOMCTL_BASE}/api/dsp/gain",
            {"bus": bus, "delta": int(delta)},
        )
    except Exception as exc:
        _set_device_error("DSP", exc)
    # redirect per ricaricare pagina e label aggiornate
    return RedirectResponse("/operator", status_code=303)


@router.post("/operator/dsp/volume")
async def op_dsp_volume(
    bus: str = Form(...),
    delta: int = Form(...),
    _=Depends(require_operator),
):
    """
    Handler per i pulsanti VOLUME +/- (bus = in_a, out0..3).
    """
    try:
        await _post(
            f"{ROOMCTL_BASE}/api/dsp/volume",
            {"bus": bus, "delta": int(delta)},
        )
    except Exception as exc:
        _set_device_error("DSP", exc)
    return RedirectResponse("/operator", status_code=303)


@router.post("/operator/dsp/recall")
async def op_dsp_recall(
    preset: str = Form(...),
    _=Depends(require_operator),
):
    """
    Handler per i pulsanti Recall preset (F00, U01, U02, U03).
    """
    try:
        await _post(
            f"{ROOMCTL_BASE}/api/dsp/recall",
            {"preset": preset},
        )
    except Exception as exc:
        _set_device_error("DSP", exc)
    return RedirectResponse("/operator", status_code=303)

@router.post('/operator/shelly/set')
async def op_shelly_set(sid:str=Form(...),on:bool=Form(...),_=Depends(require_operator)):
    try:
        await _post(f"{ROOMCTL_BASE}/api/shelly/{sid}/set",{'on':bool(on)})
    except Exception as exc:
        _set_device_error(f"Shelly {sid}", exc)
    return RedirectResponse('/operator',status_code=303)

@router.post('/operator/shelly/pulse')
async def op_shelly_pulse(sid:str=Form(...),_=Depends(require_operator)):
    #await _post(f"{ROOMCTL_BASE}/api/shelly/{sid}/set",{'on':True}); import asyncio; await asyncio.sleep(0.8); await _post(f"{ROOMCTL_BASE}/api/shelly/{sid}/set",{'on':False}); return RedirectResponse('/operator',status_code=303)
    try:
        await _post(f"{ROOMCTL_BASE}/api/shelly/{sid}/set", {'on': True})
    except Exception as exc:
        _set_device_error(f"Shelly {sid}", exc)
    return RedirectResponse('/operator', status_code=303)

@router.post('/operator/shelly/invert')
async def op_shelly_invert(req: Request, _=Depends(require_operator)):
    form = await req.form()
    values = form.getlist('inverti_corsa') if hasattr(form, 'getlist') else [form.get('inverti_corsa', '')]
    invert_val = any(str(v).lower() in ('true', '1', 'on', 'yes') for v in values)
    try:
        await _post(f"{ROOMCTL_BASE}/api/shelly/inverti_corsa", {'inverti_corsa': invert_val})
    except Exception as exc:
        _set_device_error("Shelly 2", exc)
    return RedirectResponse('/operator', status_code=303)


@router.post("/operator/power_schedule")
async def op_power_schedule(req: Request, _=Depends(require_operator)):
    form = await req.form()
    on_time = str(form.get("on_time", "")).strip()
    off_time = str(form.get("off_time", "")).strip()
    days = form.getlist("days") if hasattr(form, "getlist") else []
    enabled = str(form.get("enabled", "")).lower() in ("true", "1", "on", "yes")

    payload = {
        "on_time": on_time,
        "off_time": off_time,
        "days": days,
        "enabled": enabled,
    }

    state = get_public_state()
    try:
        await _post(f"{ROOMCTL_BASE}/api/power/schedule", payload)
        state["text"] = "Pianificazione alimentazione aggiornata"
        set_public_state(state)
    except Exception as exc:
        _set_device_error("Programmazione alimentazione", exc)
        return RedirectResponse("/operator", status_code=303)

    return RedirectResponse("/operator", status_code=303)

@router.post("/ui/special/set_datetime")
async def ui_set_datetime(req: Request, _=Depends(require_operator)):
    form = await req.form()
    date_str = str(form.get("date", "")).strip()
    time_str = str(form.get("time", "")).strip()

    state = get_public_state()

    if not date_str or not time_str:
        state["text"] = "Inserisci data e ora valide"
        set_public_state(state)
        return RedirectResponse("/operator", status_code=303)

    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        state["text"] = "Formato data/ora non valido"
        set_public_state(state)
        return RedirectResponse("/operator", status_code=303)

    try:
        await _post(
            f"{ROOMCTL_BASE}/api/special/set_datetime",
            {"datetime": dt.isoformat()},
        )
        state["text"] = "Data e ora aggiornate"
        set_public_state(state)
    except Exception as exc:
        _set_device_error("RTC", exc)
        return RedirectResponse("/operator", status_code=303)
    return RedirectResponse("/operator", status_code=303)


	

@router.post('/ui/special/reboot_terminal')
async def ui_reboot_terminal(_: bool = Depends(require_operator)):
    state = get_public_state()
    try:
        # Adatta l’URL a come il backend espone il comando di reboot
        await _post(f"{ROOMCTL_BASE}/api/special/reboot_terminal", {})
        state["text"] = "Riavvio terminale richiesto..."
        set_public_state(state)
    except Exception as exc:
        _set_device_error("Terminale", exc)
        return RedirectResponse('/', status_code=303)
    return RedirectResponse('/', status_code=303)
