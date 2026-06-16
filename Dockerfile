# FlatWrap - imagen de producción (API FastAPI + frontend estático en un proceso)
FROM python:3.12-slim

# Librerías de sistema que piden trimesh / OpenCASCADE (cascadio) en headless.
# Si el primer build falla por un .so faltante, agregar el paquete que indique.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxrender1 \
        libxext6 \
        libsm6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias primero (capa cacheable)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Código
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# El host inyecta $PORT; por defecto 8000.
ENV PORT=8000
EXPOSE 8000

# main.py resuelve el frontend como parents[2]/frontend => /app/frontend
# corriendo desde /app/backend. 1 solo worker (estado SESSION en RAM, ver nota).
CMD ["sh", "-c", "cd backend && uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 120"]
