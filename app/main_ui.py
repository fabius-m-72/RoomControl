from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .ui import router as ui_router

def mount_ui(app:FastAPI)->None:
 app.mount('/static',StaticFiles(directory='app/static'),name='static')
 app.include_router(ui_router)
