# ============================================================
# routers/predict_router.py
# ------------------------------------------------------------
# Endpoints relacionados con generacion y consulta de predicciones:
#   POST /predict                       - generar prediccion del dia
#   GET  /predict/{fecha}               - consultar prediccion existente
#   GET  /predict/{fecha}/pdf           - descargar PDF
#   GET  /predict/{fecha}/csv           - descargar CSV
#   POST /predict/{fecha}/signoff       - firma del gerente
#   GET  /predict/{fecha}/signoff       - consultar firma
# ============================================================

from datetime import date
from io import StringIO
from typing import Annotated
import base64
import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from auth import get_current_user, require_roles
from config import get_settings
from database import get_connection, log_audit, rows_to_list
from schemas.predict import (
    PredictRequest, PredictResponse, SKUPrediction,
    SignoffRequest, SignoffResponse,
)
from services.feature_engineering import es_dia_especial as _es_especial
from services.feature_engineering import es_quincena as _es_quincena
from services.llm_service import (
    obtener_alertas_contextuales, formatear_alertas_para_pdf,
)
from services.pdf_renderer import generar_pdf_orden
from services.prediction_service import generar_prediccion_dia

router = APIRouter(prefix="/predict", tags=["predict"])


def _cargar_snapshot_o_csv(csv_b64: str | None) -> pd.DataFrame:
    """
    Si csv_b64 es None, carga el snapshot original del Freeze.
    Si viene, lo decodifica y lo usa como historia.
    """
    settings = get_settings()
    if csv_b64 is None or csv_b64.strip() == "":
        path = settings.MODEL_ORIGINAL_DIR / settings.SNAPSHOT_ORIGINAL_FILENAME
        if not path.exists():
            raise HTTPException(
                status_code=500,
                detail=(
                    f"No hay snapshot original en {path}. Carga el CSV via "
                    "snapshot_csv_b64 o coloca df_ml_ready_for_M3.csv en "
                    "data/models/original/"
                ),
            )
        df = pd.read_csv(path)
    else:
        try:
            raw = base64.b64decode(csv_b64)
            df = pd.read_csv(io.BytesIO(raw))
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo decodificar el CSV base64: {e}",
            )

    df["Fecha"] = pd.to_datetime(df["Fecha"])
    # Asegurar columnas minimas
    requeridas = {"Fecha", "SKU_Operativo", "Venta_Real", "Categoria", "Factor_Impacto_Total"}
    faltantes = requeridas - set(df.columns)
    if faltantes:
        raise HTTPException(
            status_code=400,
            detail=f"CSV no tiene columnas requeridas: {faltantes}",
        )
    # Si trae Bloque_Horario, agregar a diario
    if "Bloque_Horario" in df.columns:
        df = (
            df.groupby(["Fecha", "SKU_Operativo", "Categoria"], as_index=False)
            .agg({"Venta_Real": "sum", "Factor_Impacto_Total": "first"})
        )
    return df


@router.post(
    "",
    response_model=PredictResponse,
    summary="Generar la orden de surtido para una fecha",
)
async def predecir(
    payload: PredictRequest,
    request: Request,
    user: Annotated[dict, Depends(require_roles("admin", "manager", "sinodal"))],
):
    """
    Genera la prediccion completa para la fecha objetivo. Incluye:
    feature engineering, prediccion LightGBM, aplicacion de
    guardrails GR1-GR4, conversion a botellas, refuerzo vespertino,
    PDF, CSV, log estructurado, y alertas contextuales del LLM.

    Si el modelo principal falla, cae automaticamente al baseline
    PM 4 semanas (fallback) y marca fallback_used=True.
    """
    settings = get_settings()
    df_hist = _cargar_snapshot_o_csv(payload.snapshot_csv_b64)

    # 1. Generar prediccion
    try:
        resultado = generar_prediccion_dia(
            payload.prediction_date, df_hist, user["id"],
        )
    except Exception as e:
        log_audit(
            user["id"], "predict_failed",
            endpoint="/predict",
            ip_address=request.client.host if request.client else None,
            details={"date": str(payload.prediction_date), "error": str(e)[:300]},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error al generar prediccion: {type(e).__name__}: {e}",
        )

    # 2. Alertas del LLM (opcional, fallback silencioso)
    alertas_dict = None
    alertas_texto = None
    if payload.include_llm_alerts:
        try:
            alertas_dict = obtener_alertas_contextuales(
                payload.prediction_date,
                es_festivo=_es_especial(payload.prediction_date),
                es_quincena=_es_quincena(payload.prediction_date),
                es_fin_de_semana=payload.prediction_date.weekday() >= 5,
            )
            alertas_texto = formatear_alertas_para_pdf(alertas_dict)
        except Exception:
            alertas_dict = None
            alertas_texto = None

    # 3. Renderizar PDF
    from pathlib import Path
    pdf_path = Path(resultado["pdf_path"])
    try:
        generar_pdf_orden(
            output_path=pdf_path,
            prediction_date=payload.prediction_date,
            model_version=resultado["model_version"],
            operation_mode=resultado["operation_mode"],
            predictions=resultado["predictions"],
            guardrails_summary=resultado["guardrails_summary"],
            fallback_used=resultado["fallback_used"],
            fallback_reason=resultado["fallback_reason"],
            alertas_contextuales=alertas_texto,
        )
    except Exception as e:
        # El PDF es secundario; si falla, log pero no romper la respuesta
        log_audit(
            user["id"], "pdf_render_failed",
            endpoint="/predict",
            details={"error": str(e)[:300]},
        )

    log_audit(
        user["id"], "predict_success",
        endpoint="/predict",
        ip_address=request.client.host if request.client else None,
        details={
            "date": str(payload.prediction_date),
            "model_version": resultado["model_version"],
            "fallback_used": resultado["fallback_used"],
            "n_skus": resultado["n_skus_predicted"],
        },
    )

    items = [SKUPrediction(**p) for p in resultado["predictions"]]
    return PredictResponse(
        prediction_date=resultado["prediction_date"],
        model_version=resultado["model_version"],
        operation_mode=resultado["operation_mode"],
        n_skus_predicted=resultado["n_skus_predicted"],
        total_bottles=resultado["total_bottles"],
        total_units=resultado["total_units"],
        guardrails_summary=resultado["guardrails_summary"],
        predictions=items,
        pdf_path=str(pdf_path),
        csv_path=resultado["csv_path"],
        log_path=resultado["log_path"],
        fallback_used=resultado["fallback_used"],
        fallback_reason=resultado["fallback_reason"],
        llm_alerts=alertas_texto,
    )


@router.get("/{fecha}", summary="Consultar prediccion existente")
async def consultar(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE prediction_date = ? ORDER BY sku",
            (fecha.isoformat(),),
        ).fetchall()
    if not rows:
        raise HTTPException(404, f"No hay prediccion para {fecha}")
    return {"prediction_date": fecha, "items": rows_to_list(rows)}


@router.get("/{fecha}/pdf", summary="Descargar PDF de orden de surtido")
async def descargar_pdf(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    settings = get_settings()
    pdf = settings.OUTPUTS_DIR / f"orden_surtido_{fecha.strftime('%Y%m%d')}.pdf"
    if not pdf.exists():
        raise HTTPException(404, "PDF no generado para esa fecha")
    return FileResponse(
        path=pdf, media_type="application/pdf",
        filename=f"orden_surtido_{fecha.strftime('%Y%m%d')}.pdf",
    )


@router.get("/{fecha}/csv", summary="Descargar CSV de predicciones crudas")
async def descargar_csv(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    settings = get_settings()
    csv = settings.OUTPUTS_DIR / f"predicciones_{fecha.strftime('%Y%m%d')}.csv"
    if not csv.exists():
        raise HTTPException(404, "CSV no generado para esa fecha")
    return FileResponse(
        path=csv, media_type="text/csv",
        filename=f"predicciones_{fecha.strftime('%Y%m%d')}.csv",
    )


# ------------------------------------------------------------
# Signoff - firma del gerente (Tier B: adoption signal)
# ------------------------------------------------------------
@router.post(
    "/{fecha}/signoff",
    response_model=SignoffResponse,
    summary="Firma del gerente con modificaciones opcionales",
)
async def firmar(
    fecha: date,
    payload: SignoffRequest,
    request: Request,
    user: Annotated[dict, Depends(require_roles("manager", "admin"))],
):
    """
    El gerente firma la orden del dia. Registra modificaciones si las hubo.
    La metrica 'firma sin modificaciones' es uno de los KPIs de adopcion
    del M4 (>=80% de dias).
    """
    settings = get_settings()
    if payload.prediction_date != fecha:
        raise HTTPException(400, "Fecha del path no coincide con payload")

    n_mod = len(payload.modifications)
    fecha_str = fecha.isoformat()
    with get_connection() as conn:
        # Verificar que exista prediccion
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM predictions WHERE prediction_date = ?",
            (fecha_str,),
        ).fetchone()
        if row["c"] == 0:
            raise HTTPException(404, "No hay prediccion para firmar en esa fecha")

        conn.execute(
            """
            INSERT OR REPLACE INTO signoffs (
                prediction_date, signed_by, modifications, n_modifications,
                notes, operation_mode
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                fecha_str, user["id"],
                json.dumps(payload.modifications, ensure_ascii=False),
                n_mod, payload.notes, settings.OPERATION_MODE,
            ),
        )
        signed_row = conn.execute(
            "SELECT signed_at FROM signoffs WHERE prediction_date = ?",
            (fecha_str,),
        ).fetchone()

    log_audit(
        user["id"], "signoff",
        endpoint=f"/predict/{fecha}/signoff",
        ip_address=request.client.host if request.client else None,
        details={"date": fecha_str, "n_modifications": n_mod},
    )

    return SignoffResponse(
        prediction_date=fecha,
        signed_by=user["username"],
        signed_at=signed_row["signed_at"],
        n_modifications=n_mod,
        operation_mode=settings.OPERATION_MODE,
    )


@router.get("/{fecha}/signoff", summary="Consultar firma existente")
async def consultar_firma(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.*, u.username AS signed_by_username, u.full_name AS signed_by_name
            FROM signoffs s
            LEFT JOIN users u ON u.id = s.signed_by
            WHERE s.prediction_date = ?
            """,
            (fecha.isoformat(),),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Sin firma para {fecha}")
    return dict(row)
