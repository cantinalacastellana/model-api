# ============================================================
# routers/llm_router.py - Endpoints del LLM analista
# ------------------------------------------------------------
#   GET  /llm/alerts/{fecha}            -> alerta para una fecha
#   POST /llm/alerts/refresh/{fecha}    -> regenerar (admin)
# ============================================================

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user, require_roles
from database import get_connection, log_audit
from services.feature_engineering import es_dia_especial, es_quincena
from services.llm_service import (
    obtener_alertas_contextuales, formatear_alertas_para_pdf,
    PROMPT_VERSION,
)

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get(
    "/alerts/{fecha}",
    summary="Obtener alerta contextual de una fecha (con cache)",
)
async def obtener_alerta(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    """
    Devuelve la alerta para esa fecha. Si ya existe en BD con el prompt
    actual, la retorna del cache. Si no, llama a OpenAI (con fallback
    silencioso si falla).
    """
    alerta = obtener_alertas_contextuales(
        fecha,
        es_festivo=es_dia_especial(fecha),
        es_quincena=es_quincena(fecha),
        es_fin_de_semana=fecha.weekday() >= 5,
    )
    return {
        "alert_date": fecha,
        "prompt_version": PROMPT_VERSION,
        "alerta": alerta,
        "pdf_text": formatear_alertas_para_pdf(alerta),
    }


@router.post(
    "/alerts/refresh/{fecha}",
    summary="Forzar regeneracion de alerta (rol admin)",
)
async def refrescar_alerta(
    fecha: date,
    request: Request,
    admin: Annotated[dict, Depends(require_roles("admin"))],
):
    """
    Borra la alerta cacheada y vuelve a llamar a OpenAI. Util si la
    alerta del dia se genero con informacion incompleta y se necesita
    actualizar.
    """
    fecha_str = fecha.isoformat()
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM llm_alerts WHERE alert_date = ? AND prompt_version = ?",
            (fecha_str, PROMPT_VERSION),
        )

    alerta = obtener_alertas_contextuales(
        fecha,
        es_festivo=es_dia_especial(fecha),
        es_quincena=es_quincena(fecha),
        es_fin_de_semana=fecha.weekday() >= 5,
    )

    log_audit(
        admin["id"], "llm_alert_refreshed",
        endpoint=f"/llm/alerts/refresh/{fecha}",
        ip_address=request.client.host if request.client else None,
        details={"date": fecha_str},
    )

    return {
        "alert_date": fecha,
        "alerta": alerta,
        "pdf_text": formatear_alertas_para_pdf(alerta),
    }
