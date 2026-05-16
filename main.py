# ============================================================
# main.py - Entry point de la API FastAPI
# ------------------------------------------------------------
# Levanta la app, registra routers, configura CORS, y ejecuta el
# init de BD + carga del modelo original al arranque.
#
# Ejecucion local:
#   uvicorn main:app --host 127.0.0.1 --port 8001
#
# Detras de PM2 (ver ecosystem.config.js):
#   pm2 start ecosystem.config.js
#
# Detras de nginx, ruta /model:
#   location /model/ {
#       proxy_pass http://127.0.0.1:8001/;
#       proxy_set_header Host $host;
#       proxy_set_header X-Real-IP $remote_addr;
#       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#       proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
#   }
# ============================================================

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import init_database, get_connection

from routers.auth_router import router as auth_router
from routers.predict_router import router as predict_router
from routers.logs_router import router as logs_router
from routers.drift_router import router as drift_router
from routers.admin_router import router as admin_router
from routers.llm_router import router as llm_router
from routers.metrics_router import router as metrics_router
from routers.danger_router import router as danger_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Inicializacion al arranque:
      1. Crear BD y tablas si no existen
      2. Asegurar que el modelo M3 original esta registrado y activo
         (si no, advertir; el primer admin debe correr seed_original_model.py)
    """
    settings = get_settings()
    init_database()

    # Verificar que existe modelo activo
    with get_connection() as conn:
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM model_versions WHERE is_active = 1"
        ).fetchone()
        if active["c"] == 0:
            print("=" * 60)
            print("ADVERTENCIA: No hay modelo activo en BD.")
            print("Corre: python scripts/seed_original_model.py")
            print("=" * 60)

        # Verificar que existe al menos un usuario admin
        admin_count = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()
        if admin_count["c"] == 0:
            print("=" * 60)
            print("ADVERTENCIA: No hay usuario admin. Corre:")
            print("  python scripts/init_db.py")
            print("=" * 60)

    print(f"[{datetime.now().isoformat()}] API iniciada en modo: {settings.OPERATION_MODE}")
    yield
    print(f"[{datetime.now().isoformat()}] API detenida")


settings = get_settings()
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=settings.APP_DESCRIPTION,
    lifespan=lifespan,
    # Documentacion siempre disponible bajo /docs (relativo al root path)
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS - el frontend React/Vite vive en otro origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrar routers
app.include_router(auth_router)
app.include_router(predict_router)
app.include_router(logs_router)
app.include_router(drift_router)
app.include_router(admin_router)
app.include_router(llm_router)
app.include_router(metrics_router)
app.include_router(danger_router)

@app.get("/health", tags=["health"])
async def health():
    """Health check - usado por PM2 y por monitoreo externo."""
    with get_connection() as conn:
        active = conn.execute(
            "SELECT version FROM model_versions WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "operation_mode": settings.OPERATION_MODE,
        "active_model": active["version"] if active else None,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/", tags=["health"])
async def root():
    """Bienvenida y links."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }
