# ============================================================
# schemas/admin.py - Esquemas para endpoints administrativos
# ============================================================

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class _Base(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class RetrainRequest(_Base):
    """
    Payload para iniciar reentrenamiento manual con grid search.
    Solo lo invoca rol 'admin' (data scientist).
    """
    include_staging_logs: bool = Field(
        True,
        description=(
            "Si true, fusiona los logs de ventas reales acumulados en staging/ "
            "con el dataset original antes de reentrenar. NO modifica el dataset "
            "original del Freeze; genera un dataset extendido temporal."
        ),
    )
    notes: str = Field("", max_length=1000)
    force_promote: bool = Field(
        False,
        description=(
            "Si true, promueve el mejor candidato aunque no supere al modelo actual. "
            "USO RESTRINGIDO: solo para casos donde el modelo actual esta roto."
        ),
    )


class RetrainConfigResult(_Base):
    """Resultado de una configuracion del grid."""
    config_name: str
    num_leaves: int
    learning_rate: float
    min_child_samples: int
    lambda_l2: float
    wape_ponderado_val: float
    wape_simple_val: float
    iterations_trained: int
    duration_seconds: float


class RetrainResponse(_Base):
    """Respuesta del endpoint /admin/retrain."""
    job_id: int
    status: Literal["completed", "failed", "aborted"]
    started_at: str
    finished_at: str
    duration_seconds: float
    configs_tested: list[RetrainConfigResult]
    best_config_name: str | None
    best_wape_val: float | None
    current_wape_val: float | None
    decision: Literal["promote", "reject", "manual_review"] | None
    new_model_version: str | None
    notes: str


class ModelVersionResponse(_Base):
    """Una version del modelo registrada."""
    id: int
    version: str
    is_active: bool
    is_original: bool
    wape_val: float | None
    wape_test: float | None
    config_name: str | None
    trained_at: str
    promoted_at: str | None
    notes: str | None


class ResetResponse(_Base):
    """Respuesta de /admin/reset."""
    reset_at: str
    previous_active_version: str | None
    restored_version: str = "original"
    message: str


class PromoteRequest(_Base):
    """Promover una version especifica (uso administrativo)."""
    version: str
    notes: str = Field("", max_length=500)


class DriftStatusResponse(_Base):
    """Estado actual de drift. El frontend muestra alerta si alert=True."""
    last_check_date: str | None
    data_drift_pvalue: float | None
    model_drift_ratio: float | None
    pipeline_health: float | None
    alert_triggered: bool
    alert_reasons: list[str]
    recommendation: str
    # IMPORTANTE: nunca dispara reentrene; solo alerta al admin
