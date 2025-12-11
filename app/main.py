from fastapi import FastAPI,WebSocket
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from app.main_ui import mount_ui
from app.state import get_public_state
from app.api import router as api_router

app=FastAPI()
mount_ui(app)
app.include_router(api_router, prefix="/api")
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

@app.websocket('/ws')
async def ws(ws:WebSocket):
 await ws.accept()
 try:
  while True:
   await ws.send_json(get_public_state()); await asyncio.sleep(2.0)
 except Exception:
  pass
 finally:
  try: await ws.close()
  except Exception: pass

