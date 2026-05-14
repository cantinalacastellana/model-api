# ============================================================
# schemas/predict.py - Esquemas para prediccion y orden de surtido
# ============================================================

from datetime import date
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class _Base(BaseModel):
    """Base que desactiva el namespace protegido 'model_' (lo usamos)."""
    model_config = ConfigDict(protected_namespaces=())


class PredictRequest(_Base):
    """
    Payload para solicitar la prediccion de un dia.
    Si snapshot_csv_b64 viene vacio, la API usa el snapshot original
    del M3 (default para shadow mode con datos historicos).
    """
    prediction_date: date = Field(..., description="Fecha objetivo (YYYY-MM-DD)")
    snapshot_csv_b64: str | None = Field(
        None,
        description=(
            "CSV de ventas historicas hasta T-1 codificado en base64. "
            "Opcional: si se omite, se usa el snapshot del Freeze M3."
        ),
    )
    include_llm_alerts: bool = Field(
        True,
        description="Si true, agrega alertas contextuales del LLM al PDF",
    )


class SKUPrediction(_Base):
    """Una prediccion por (fecha, SKU) con guardrails aplicados."""
    sku: str
    categoria: str | None = None
    pred_raw: float
    pred_final: float
    guardrails_applied: list[str]  # ["GR1","GR3"], etc.
    bottles: int
    refuerzo_vespertino: bool


class PredictResponse(_Base):
    """Respuesta del endpoint /predict."""
    prediction_date: date
    model_version: str
    operation_mode: str
    n_skus_predicted: int
    total_bottles: int
    total_units: float
    guardrails_summary: dict[str, int]  # {"GR1": 12, "GR2": 3, ...}
    predictions: list[SKUPrediction]
    pdf_path: str
    csv_path: str
    log_path: str
    fallback_used: bool = False
    fallback_reason: str | None = None
    llm_alerts: str | None = None


class SignoffRequest(_Base):
    """Payload para firmar la orden del dia."""
    prediction_date: date
    modifications: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description=(
            'Diccionario de modificaciones {SKU: {"original": int, "modified": int}}. '
            "Vacio si no se modifico nada (caso ideal: firma sin modificaciones)."
        ),
    )
    notes: str = Field("", max_length=2000)


class SignoffResponse(_Base):
    """Confirmacion de firma."""
    prediction_date: date
    signed_by: str
    signed_at: str
    n_modifications: int
    operation_mode: str


class NotaBarraRequest(_Base):
    """
    Registro de una nota a barra (reposicion intra-dia). Esta es la
    metrica clave de impacto para Tier B.
    """
    nota_date: date
    sku: str = Field(..., min_length=1, max_length=120)
    quantity: int = Field(..., ge=1)
    bloque_horario: Literal["DEMANDA ALTA", "DEMANDA MEDIA", "DEMANDA BAJA"] | None = None
    reason: str = Field("", max_length=500)


class ActualSaleRequest(_Base):
    """Registro de venta real al cierre de jornada."""
    sale_date: date
    sku: str
    units_sold: int = Field(..., ge=0)
    factor_impacto: float | None = None


class DailyComparisonResponse(_Base):
    """Comparacion shadow del dia: prediccion vs real vs notas."""
    comparison_date: date
    n_skus_predicted: int
    n_skus_with_sales: int
    n_notas_barra: int
    total_units_predicted: float
    total_units_sold: float
    wape_simple_observed: float | None
    wape_ponderado_observed: float | None
    notas_vs_baseline: dict | None
