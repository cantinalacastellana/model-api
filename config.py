# ============================================================
# config.py - Configuracion central de la API
# ------------------------------------------------------------
# Toda la configuracion lee de variables de entorno con valores
# por defecto razonables para desarrollo. En produccion, definir
# las variables en el .env o en el ecosystem.config.js de PM2.
# ============================================================

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuracion global de la API. Se instancia una sola vez
    al arranque (singleton via get_settings()).
    """

    # ----- Identidad de la API -----
    APP_NAME: str = "API Cantina La Castellana - Prediccion de Demanda"
    APP_VERSION: str = "1.0.0"
    APP_DESCRIPTION: str = (
        "Wrapper FastAPI del modelo LightGBM M3 (Jose Emilio Kuri Otero). "
        "Genera ordenes diarias de surtido, monitorea drift, y soporta "
        "reentrenamiento manual con grid search auditado."
    )

    # ----- Servidor -----
    # Puerto distinto al de Node.js (que ya usa 3000 para /menu y /chat).
    HOST: str = "127.0.0.1"
    PORT: int = 8001
    ROOT_PATH: str = "/model"  # Prefijo bajo el cual nginx la enruta

    # ----- Autenticacion JWT -----
    # En produccion, CAMBIAR el SECRET_KEY con: openssl rand -hex 32
    JWT_SECRET_KEY: str = "CAMBIAR-EN-PROD-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # Jornada laboral

    # ----- Rutas de archivos -----
    BASE_DIR: Path = Path(__file__).resolve().parent
    DATA_DIR: Path = BASE_DIR / "data"
    MODELS_DIR: Path = DATA_DIR / "models"
    MODEL_ORIGINAL_DIR: Path = MODELS_DIR / "original"  # Snapshot M3 inmutable
    MODEL_CURRENT_DIR: Path = MODELS_DIR / "current"    # Modelo activo
    MODEL_CANDIDATES_DIR: Path = MODELS_DIR / "candidates"
    LOGS_DIR: Path = DATA_DIR / "logs"
    OUTPUTS_DIR: Path = DATA_DIR / "outputs"
    STAGING_DIR: Path = DATA_DIR / "staging"  # Logs nuevos pendientes de promover a dataset

    # ----- Base de datos SQLite -----
    DB_PATH: Path = DATA_DIR / "castellana.db"

    # ----- Modelo: nombre del archivo serializado -----
    MODEL_FILENAME: str = "lgbm_v1.joblib"
    BASELINE_FILENAME: str = "baseline_pm4w.joblib"  # Fallback

    # ----- Snapshot original (referencia para reset) -----
    SNAPSHOT_ORIGINAL_FILENAME: str = "df_ml_ready_for_M3.csv"

    # ----- Umbrales operativos (del Freeze M3) -----
    UMBRAL_MEJORA_VS_BASELINE: float = 0.15  # 15% requerido por el Freeze
    UMBRAL_DRIFT_DATA_PVALUE: float = 0.01   # KS test
    UMBRAL_DRIFT_MODEL_RATIO: float = 1.20   # WAPE observado/esperado
    UMBRAL_PIPELINE_HEALTH: float = 0.95     # Tasa de exito mínima

    # ----- Guardrails (de la Seccion 4.4 / Tabla del M3) -----
    GR1_FACTOR_DIA_ESPECIAL: float = 1.30    # Multiplicador en festivos
    GR2_FACTOR_CAP_OUTLIER: float = 2.00     # Tope vs max historico
    GR3_DIAS_COLD_START: int = 30            # SKUs nuevos sin historia
    GR4_FREQ_LONG_TAIL: float = 0.30         # Frecuencia minima en 30d
    GR4_PRED_LONG_TAIL: float = 2.0          # Tope para suprimir

    # ----- Grid search (failsafe del reentrene) -----
    # Configuraciones a explorar. Las 5 primeras son H2.1-H2.5 documentadas
    # en el M3; las dos extras amplian el espacio sin salirse del rango
    # validado por el profesor.
    GRID_CONFIGS: list = [
        # name, num_leaves, learning_rate, min_child_samples, lambda_l2
        ("H2.1", 31, 0.05, 20, 0.1),
        ("H2.2", 63, 0.05, 10, 0.1),
        ("H2.3", 127, 0.03, 5, 1.0),
        ("H2.4", 31, 0.03, 20, 0.5),
        ("H2.5", 63, 0.02, 15, 0.3),
        ("H2.6", 47, 0.025, 18, 0.4),  # Variante intermedia
        ("H2.7", 95, 0.04, 8, 0.7),   # Mas capacidad regularizada
    ]
    EARLY_STOPPING_ROUNDS: int = 100
    SEED: int = 232106  # Misma seed del Freeze M3

    # ----- OpenAI (alertas contextuales) -----
    OPENAI_API_KEY: str = ""  # Obligatorio definir en .env
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: int = 30  # segundos
    # Si OpenAI falla, devolver respuesta vacia (NO bloquea la prediccion)
    LLM_FAIL_SILENTLY: bool = True

    # ----- CORS (frontend React Vite TypeScript) -----
    CORS_ORIGINS: list = [
        "http://localhost:5173",         # Vite dev server
        "http://localhost:4173",         # Vite preview
        "https://cantinalcastellana.com",
        "https://www.cantinalcastellana.com",
        "https://app.cantinalcastellana.com",
    ]

    # ----- Modo de operacion -----
    # 'shadow' = el PDF se archiva pero no se entrega al corredor
    # 'piloto' = el PDF se entrega; se sigue capturando notas
    # 'produccion' = operacion plena
    OPERATION_MODE: str = "shadow"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


# Singleton (FastAPI lo inyecta via Depends)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Retorna la instancia unica de Settings. Crea las carpetas si faltan."""
    global _settings
    if _settings is None:
        _settings = Settings()
        # Asegurar que las carpetas existen
        for d in [
            _settings.DATA_DIR,
            _settings.MODELS_DIR,
            _settings.MODEL_ORIGINAL_DIR,
            _settings.MODEL_CURRENT_DIR,
            _settings.MODEL_CANDIDATES_DIR,
            _settings.LOGS_DIR,
            _settings.OUTPUTS_DIR,
            _settings.STAGING_DIR,
        ]:
            d.mkdir(parents=True, exist_ok=True)
    return _settings
