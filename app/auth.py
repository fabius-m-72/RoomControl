import os,secrets,yaml
from typing import Optional
from fastapi import Request,Response,HTTPException

try:
 from argon2 import PasswordHasher
 _ph=PasswordHasher()
except Exception:
 _ph=None
_VALID_TOKENS=set(); CONFIG_MAIN=os.environ.get('ROOMCTL_CONFIG','/opt/roomctl/config/config.yaml')

def _load_config():
 try:
  import yaml
  with open(CONFIG_MAIN,'r',encoding='utf-8') as f: return yaml.safe_load(f) or {}
 except FileNotFoundError:
  return {}
 except Exception:
  return {}

def _check_pin(pin:str)->bool:
 cfg=_load_config(); auth=cfg.get('auth',{}); pin_plain=str(auth.get('pin_plain') or ''); pin_hash=auth.get('pin_hash')
 if pin_hash and _ph:
  try: _ph.verify(pin_hash,pin); return True
  except Exception: return False
 return (pin_plain!='' and pin==pin_plain)

def issue_token()->str:
 import secrets
 t=secrets.token_urlsafe(32); _VALID_TOKENS.add(t); return t

def get_token_from_cookie(request:Request)->Optional[str]:
 return request.cookies.get('rtoken')

async def require_operator(request:Request):
 t=get_token_from_cookie(request)
 if not t or t not in _VALID_TOKENS: raise HTTPException(status_code=302,detail='Redirect',headers={'Location':'/'})
 return True

async def login_with_pin(pin:str)->Optional[str]:
 if not pin: return None
 if _check_pin(pin): return issue_token()
 return None

def logout(resp:Response):
 resp.delete_cookie('rtoken'); _VALID_TOKENS.clear()
