# ============================================================
# routers/drift_router.py
# ------------------------------------------------------------
# Endpoints de monitoreo de drift:
#
#   POST /drift/check                -> ejecutar chequeo (admin)
#   GET  /drift/status               -> ultimo estado (frontend)
#   GET  /drift/history              -> historial de chequeos
#
# IMPORTANTE: NUNCA dispara reentrene. Solo escribe alertas.
# El admin las ve en el frontend y decide invocar /admin/retrain
# manualmente. Esto cumple con la Seccion 4.4 del M3 / M4 (accion
# ante falla: en este caso, alerta + revision humana).
# ============================================================

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user, require_roles
from database import get_connection, log_audit, rows_to_list
from schemas.admin import DriftStatusResponse
from services.drift_service import ejecutar_chequeo_drift_completo

router = APIRouter(prefix="/drift", tags=["drift"])


@router.post(
    "/check",
    summary="Ejecutar chequeo de drift (admin)",
)
async def ejecutar_check(
    request: Request,
    admin: Annotated[dict, Depends(require_roles("admin"))],
):
    """
    Corre los tres chequeos (data drift KS, model drift ratio, pipeline
    health) y persiste el resultado. No bloquea ni dispara reentrene.
    """
    try:
        resultado = ejecutar_chequeo_drift_completo()
    except Exception as e:
        raise HTTPException(500, f"Error en chequeo: {type(e).__name__}: {e}")

    log_audit(
        admin["id"], "drift_check_executed",
        endpoint="/drift/check",
        ip_address=request.client.host if request.client else None,
        details={
            "alert_triggered": resultado["alert_triggered"],
            "n_alerts": len(resultado["alert_reasons"]),
        },
    )
    return resultado


@router.get(
    "/status",
    response_model=DriftStatusResponse,
    summary="Estado actual de drift (frontend lo consulta)",
)
async def estado_actual(
    user: Annotated[dict, Depends(get_current_user)],
):
    """
    Retorna el ultimo chequeo registrado. Si no hay ninguno, indica
    que se debe ejecutar /drift/check.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM drift_checks ORDER BY check_date DESC LIMIT 1"
        ).fetchone()

    if row is None:
        return DriftStatusResponse(
            last_check_date=None,
            data_drift_pvalue=None,
            model_drift_ratio=None,
            pipeline_health=None,
            alert_triggered=False,
            alert_reasons=[],
            recommendation="No hay chequeos previos. Ejecuta POST /drift/check.",
        )

    import json
    reasons = json.loads(row["alert_reasons"] or "[]")
    alert = bool(row["alert_triggered"])

    if alert:
        recommendation = (
            "ALERTA: drift detectado. El data scientist debe revisar e "
            "invocar manualmente POST /admin/retrain si procede. El modelo "
            "actual continua activo."
        )
    else:
        recommendation = "Modelo estable. Sin acciones requeridas."

    return DriftStatusResponse(
        last_check_date=row["check_date"],
        data_drift_pvalue=row["data_drift_pvalue"],
        model_drift_ratio=row["model_drift_ratio"],
        pipeline_health=row["pipeline_health"],
        alert_triggered=alert,
        alert_reasons=reasons,
        recommendation=recommendation,
    )


@router.get("/history", summary="Historial de chequeos de drift")
async def historial(
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = 30,
):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM drift_checks ORDER BY check_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"count": len(rows), "checks": rows_to_list(rows)}
