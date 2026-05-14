# ============================================================
# database.py - Capa de acceso a SQLite
# ------------------------------------------------------------
# Una sola base de datos castellana.db con todas las tablas
# operativas. Se usa sqlite3 nativo (sin ORM) por simplicidad
# y para que sea facil inspeccionar la BD a mano cuando los
# sinodales auditen la entrega.
#
# Tablas:
#   users           -> autenticacion y roles
#   predictions     -> cada prediccion diaria por SKU
#   signoffs        -> firmas del gerente (con modificaciones)
#   notas_barra     -> notas a barra reales (KPI Tier B)
#   actual_sales    -> ventas registradas (comparacion shadow)
#   drift_checks    -> historial de alertas de drift
#   retrain_jobs    -> reentrenamientos manuales con grid search
#   model_versions  -> registro de modelos disponibles
#   llm_alerts      -> alertas contextuales del LLM
#   audit_log       -> bitacora de auditoria
# ============================================================

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from config import get_settings


# ------------------------------------------------------------
# Esquema SQL
# ------------------------------------------------------------
# Cada tabla incluye comentarios para que cualquier auditor
# entienda su proposito sin abrir la documentacion.
# ------------------------------------------------------------
SCHEMA_SQL = """
-- Usuarios del sistema (autenticacion)
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('admin','manager','barman','sinodal')),
    full_name       TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at   TEXT
);

-- Una fila por (fecha, SKU) cada vez que se corre la prediccion.
-- guardrails_applied es JSON: ["GR1","GR2","GR4"] o []
CREATE TABLE IF NOT EXISTS predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_date     TEXT NOT NULL,
    sku                 TEXT NOT NULL,
    categoria           TEXT,
    pred_raw            REAL NOT NULL,
    pred_final          REAL NOT NULL,
    guardrails_applied  TEXT NOT NULL DEFAULT '[]',
    bottles             INTEGER,
    refuerzo_vespertino INTEGER NOT NULL DEFAULT 0,
    model_version       TEXT NOT NULL,
    operation_mode      TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    created_by          INTEGER REFERENCES users(id),
    UNIQUE(prediction_date, sku, model_version)
);
CREATE INDEX IF NOT EXISTS idx_pred_date ON predictions(prediction_date);
CREATE INDEX IF NOT EXISTS idx_pred_sku ON predictions(sku);

-- Firma del gerente sobre la orden del dia.
-- modifications es JSON: {"SKU_X": {"original": 12, "modified": 8}}
CREATE TABLE IF NOT EXISTS signoffs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_date     TEXT NOT NULL UNIQUE,
    signed_by           INTEGER NOT NULL REFERENCES users(id),
    signed_at           TEXT NOT NULL DEFAULT (datetime('now')),
    modifications       TEXT NOT NULL DEFAULT '{}',
    n_modifications     INTEGER NOT NULL DEFAULT 0,
    notes               TEXT,
    operation_mode      TEXT NOT NULL
);

-- Notas a barra registradas durante la jornada. ESTA es la metrica
-- clave para Tier B (impact proxy). Cada fila = una reposicion
-- intra-dia que tuvo que hacer el corredor.
CREATE TABLE IF NOT EXISTS notas_barra (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nota_date       TEXT NOT NULL,
    sku             TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    bloque_horario  TEXT,
    reason          TEXT,
    reported_by     INTEGER REFERENCES users(id),
    reported_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notas_date ON notas_barra(nota_date);

-- Ventas reales del dia (insumo para comparacion shadow y para
-- agregar al staging si se va a reentrenar).
CREATE TABLE IF NOT EXISTS actual_sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_date       TEXT NOT NULL,
    sku             TEXT NOT NULL,
    units_sold      INTEGER NOT NULL,
    factor_impacto  REAL,
    reported_by     INTEGER REFERENCES users(id),
    reported_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(sale_date, sku)
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON actual_sales(sale_date);

-- Resultados de los chequeos de drift. NUNCA dispara reentrene
-- automaticamente; solo escribe alert_triggered=1 para que el
-- frontend muestre la alerta y el admin decida.
CREATE TABLE IF NOT EXISTS drift_checks (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    check_date              TEXT NOT NULL,
    data_drift_ks_stat      REAL,
    data_drift_pvalue       REAL,
    model_drift_wape_obs    REAL,
    model_drift_wape_exp    REAL,
    model_drift_ratio       REAL,
    pipeline_health         REAL,
    alert_triggered         INTEGER NOT NULL DEFAULT 0,
    alert_reasons           TEXT NOT NULL DEFAULT '[]',
    checked_at              TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(check_date)
);

-- Reentrenamiento manual con grid search. Cada job prueba varias
-- configuraciones (GRID_CONFIGS), elige la mejor en holdout, y
-- solo promueve si supera al modelo activo. SI NO, fallback al
-- modelo previo (no se promueve nada).
CREATE TABLE IF NOT EXISTS retrain_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_by          INTEGER NOT NULL REFERENCES users(id),
    started_at          TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at         TEXT,
    status              TEXT NOT NULL DEFAULT 'running'
                        CHECK(status IN ('running','completed','failed','aborted')),
    configs_tested      TEXT NOT NULL DEFAULT '[]',
    best_config_name    TEXT,
    best_wape_val       REAL,
    current_wape_val    REAL,
    decision            TEXT CHECK(decision IN ('promote','reject','manual_review')),
    new_model_version   TEXT,
    error_message       TEXT,
    notes               TEXT
);

-- Versiones del modelo guardadas. La fila is_active=1 es la que
-- el endpoint /predict usa. La version 'original' nunca se borra;
-- es el modelo M3 entregado a los sinodales (target del /reset).
CREATE TABLE IF NOT EXISTS model_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         TEXT UNIQUE NOT NULL,
    path            TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 0,
    is_original     INTEGER NOT NULL DEFAULT 0,
    wape_val        REAL,
    wape_test       REAL,
    config_name     TEXT,
    config_params   TEXT,
    trained_at      TEXT NOT NULL DEFAULT (datetime('now')),
    promoted_at     TEXT,
    notes           TEXT
);

-- Alertas contextuales generadas por el LLM (OpenAI). Solo informativo,
-- NO modifica la prediccion. Se guarda para auditoria del prompt usado.
CREATE TABLE IF NOT EXISTS llm_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_date      TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    raw_response    TEXT NOT NULL,
    sources         TEXT NOT NULL DEFAULT '[]',
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    fallback_used   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(alert_date, prompt_version)
);

-- Bitacora de auditoria. Cualquier endpoint sensible escribe aqui:
-- login, prediccion, firma, reentrene, reset, promocion.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    action      TEXT NOT NULL,
    endpoint    TEXT,
    ip_address  TEXT,
    details     TEXT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);
"""


def get_db_path() -> Path:
    """Helper para obtener la ruta de la BD desde settings."""
    return get_settings().DB_PATH


def init_database() -> None:
    """
    Crea la BD si no existe y aplica el esquema.
    Llamar una vez al arranque (en main.py / lifespan).
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()


@contextmanager
def get_connection():
    """
    Context manager para obtener una conexion sqlite3 con:
    - row_factory configurado a sqlite3.Row (acceso por nombre)
    - foreign_keys ON
    - commit automatico al salir sin error, rollback si excepcion
    """
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------
# Helpers de auditoria - se usan desde todos los routers
# ------------------------------------------------------------
def log_audit(
    user_id: int | None,
    action: str,
    endpoint: str | None = None,
    ip_address: str | None = None,
    details: dict | None = None,
) -> None:
    """
    Inserta una fila en audit_log. Se llama desde cualquier endpoint
    sensible para dejar rastro auditable (Tier A + sinodales).
    """
    details_json = json.dumps(details, ensure_ascii=False) if details else None
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit_log (user_id, action, endpoint, ip_address, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, action, endpoint, ip_address, details_json),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convierte sqlite3.Row a dict para serializar como JSON."""
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_list(rows: list[sqlite3.Row]) -> list[dict]:
    """Idem para listas."""
    return [row_to_dict(r) for r in rows]
