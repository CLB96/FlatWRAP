"""FlatWrap - Generador de patrones 2D para wrapping de piezas automotrices.

Punto de entrada del backend. Sirve la API y el frontend estático
desde un solo proceso para correr todo con un comando:

    uvicorn app.main:app --reload --port 8000

Luego abrir http://localhost:8000 en el navegador.
"""

import base64
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# Contraseña opcional: si FLATWRAP_PASSWORD está definida (en el host) se exige
# HTTP Basic en TODAS las rutas. Sin la variable, la app queda abierta (local).
_PASSWORD = os.environ.get("FLATWRAP_PASSWORD")

app = FastAPI(
    title="FlatWrap",
    description="3D -> patrón 2D para wrapping (die-press premium wrapping)",
    version="0.1.0",
)


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    """Puerta de contraseña simple (HTTP Basic) cuando hay FLATWRAP_PASSWORD."""
    if _PASSWORD:
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
                ok = secrets.compare_digest(pw, _PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="FlatWrap"'},
            )
    return await call_next(request)


# CORS abierto: app local de un solo usuario
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# El frontend se sirve al final para no interceptar /api
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
