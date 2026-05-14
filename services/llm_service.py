# ============================================================
# services/llm_service.py - Agente LLM analista (OpenAI)
# ------------------------------------------------------------
# Sigue exactamente la estructura del M3 (Seccion 7, "Capa de
# producto: frontend React + LLM analista"):
#
#   - El LLM NO modifica la prediccion ni la orden
#   - Solo LEE eventos del dia (calendario CDMX, clima, marchas,
#     partidos, eventos cercanos, bloqueos viales)
#   - Produce una seccion de "Alertas contextuales" informativa
#   - Controles: fuentes registradas en logs para auditoria,
#     prompt versionado con casos fijos en CI, fallback SILENCIOSO
#     si el LLM falla
#
# El prompt usa la estructura base de OpenAI Responses (Chat
# Completions API). Versionado por PROMPT_VERSION para que cualquier
# cambio quede trazado.
# ============================================================

from __future__ import annotations
from datetime import date
import json

from config import get_settings
from database import get_connection


# Version del prompt. Cualquier cambio al prompt debe incrementar este
# numero (esto es lo que se valida en CI con casos fijos).
PROMPT_VERSION = "v1.0.0"

SYSTEM_PROMPT = """Eres un analista contextual que asiste al gerente de Cantina La Castellana \
(Antonio Caso 58, Col. San Rafael, Ciudad de Mexico, fundada en 1892). Tu tarea es \
identificar eventos del dia objetivo que puedan afectar la demanda del bar, SIN \
modificar predicciones del modelo de ML.

Reportas SOLO informacion factica sobre:
- Calendario civico/religioso de CDMX (festivos, dias de pago, eventos masivos)
- Clima esperado (lluvia, frio extremo, ola de calor)
- Eventos deportivos televisados (futbol, box, NFL playoff/super bowl)
- Manifestaciones, marchas o bloqueos viales reportados publicamente
- Eventos culturales cercanos a la Col. San Rafael / centro CDMX

Formato de salida estricto (JSON):
{
  "alertas": [
    {"tipo": "...", "descripcion": "...", "impacto_estimado": "alto|medio|bajo", "fuente": "..."},
    ...
  ],
  "recomendacion_gerente": "Texto breve (1-2 frases) accionable; NO modificar prediccion."
}

Si no hay eventos relevantes, retorna {"alertas": [], "recomendacion_gerente": "Dia sin \
eventos contextuales relevantes."}.

NO inventes eventos. NO modifiques la prediccion del modelo. NO sugieras cifras de stock. \
Tu rol es exclusivamente informativo."""


USER_PROMPT_TEMPLATE = """Fecha objetivo: {fecha}
Dia de la semana: {dia_semana}
Es festivo registrado: {es_festivo}
Es quincena: {es_quincena}
Es fin de semana: {es_fdsem}

Reporta alertas contextuales para esta fecha en JSON estructurado segun el formato \
especificado en el system prompt."""


def _fallback_alertas() -> dict:
    """Respuesta cuando OpenAI falla. NO bloquea la prediccion."""
    return {
        "alertas": [],
        "recomendacion_gerente": (
            "No se pudo consultar al agente contextual. La orden del dia "
            "se basa unicamente en el modelo y guardrails. Revisar manualmente "
            "si hay eventos extraordinarios."
        ),
        "fallback_used": True,
    }


def obtener_alertas_contextuales(
    fecha_objetivo: date,
    es_festivo: bool,
    es_quincena: bool,
    es_fin_de_semana: bool,
) -> dict:
    """
    Llama a OpenAI con el prompt versionado. Si falla, retorna fallback
    silencioso. Persiste la respuesta cruda en BD (tabla llm_alerts)
    para auditoria.
    """
    settings = get_settings()
    fecha_str = fecha_objetivo.isoformat()

    # 1. Si ya existe alerta para esa fecha+prompt_version, devolverla
    with get_connection() as conn:
        row = conn.execute(
            "SELECT raw_response FROM llm_alerts WHERE alert_date = ? AND prompt_version = ?",
            (fecha_str, PROMPT_VERSION),
        ).fetchone()
        if row:
            try:
                return json.loads(row["raw_response"])
            except json.JSONDecodeError:
                pass  # respuesta corrupta, reintentar

    # 2. Si no hay API key, fallback inmediato (NO bloquear)
    if not settings.OPENAI_API_KEY:
        result = _fallback_alertas()
        _persistir_alerta(fecha_str, json.dumps(result), [], fallback=True)
        return result

    # 3. Llamar a OpenAI
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT)

        dia_semana_es = {
            0: "Lunes", 1: "Martes", 2: "Miercoles", 3: "Jueves",
            4: "Viernes", 5: "Sabado", 6: "Domingo",
        }[fecha_objetivo.weekday()]

        user_prompt = USER_PROMPT_TEMPLATE.format(
            fecha=fecha_str,
            dia_semana=dia_semana_es,
            es_festivo="Si" if es_festivo else "No",
            es_quincena="Si" if es_quincena else "No",
            es_fdsem="Si" if es_fin_de_semana else "No",
        )

        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,  # baja para consistencia
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        parsed["fallback_used"] = False
        _persistir_alerta(fecha_str, content, parsed.get("alertas", []), fallback=False)
        return parsed

    except Exception as e:
        # Fallback silencioso por instruccion del M3
        if settings.LLM_FAIL_SILENTLY:
            result = _fallback_alertas()
            result["error_internal"] = f"{type(e).__name__}: {str(e)[:200]}"
            _persistir_alerta(fecha_str, json.dumps(result), [], fallback=True)
            return result
        raise


def _persistir_alerta(
    fecha_str: str, raw_response: str, sources: list, fallback: bool
) -> None:
    """Guarda la respuesta del LLM para auditoria (Seccion 3.6 M4)."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO llm_alerts (
                alert_date, prompt_version, raw_response, sources, fallback_used
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                fecha_str, PROMPT_VERSION, raw_response,
                json.dumps(sources, ensure_ascii=False), int(fallback),
            ),
        )


def formatear_alertas_para_pdf(alertas_dict: dict) -> str:
    """
    Convierte el dict del LLM a texto plano para insertar al PDF.
    Si no hay alertas, retorna mensaje neutro.
    """
    alertas = alertas_dict.get("alertas", [])
    rec = alertas_dict.get("recomendacion_gerente", "")
    if not alertas:
        return f"ALERTAS CONTEXTUALES: {rec}"
    lineas = ["ALERTAS CONTEXTUALES:"]
    for a in alertas:
        lineas.append(
            f"- [{a.get('impacto_estimado', '?').upper()}] {a.get('tipo', '')}: "
            f"{a.get('descripcion', '')} (Fuente: {a.get('fuente', 'N/A')})"
        )
    lineas.append(f"\nRecomendacion: {rec}")
    return "\n".join(lineas)
