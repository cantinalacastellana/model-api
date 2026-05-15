# ============================================================
# services/llm_service.py - Agente LLM analista (OpenAI)
# ------------------------------------------------------------
# Sigue la estructura del M3 (Seccion 7, "Capa de producto:
# frontend React + LLM analista"):
#
#   - El LLM NO modifica la prediccion ni la orden
#   - Solo LEE el contexto del dia y emite alertas informativas
#   - Fallback SILENCIOSO si el LLM falla
#
# DECISION DE DISENO (v2.0.0):
# El prompt v1 fallaba porque pedia eventos en tiempo real
# (clima exacto, partidos del dia, marchas) que requieren acceso
# a internet. gpt-4o-mini sin web search responde "alertas: []"
# correctamente al no inventar.
#
# v2 cambia el enfoque a CONOCIMIENTO ESTATICO que el LLM si
# tiene:
#   - Festividades fijas del calendario mexicano
#   - Patrones culturales en cantinas tradicionales
#   - Estacionalidad climatologica generica de CDMX
#   - Significado cultural del dia de la semana
#
# Ademas, server-side se enriquece el contexto antes de mandarlo
# al LLM, asegurando que detecte San Valentin, Quincena, etc.
# aunque el LLM por si solo no las haya considerado.
# ============================================================

from __future__ import annotations
from datetime import date
import json

from config import get_settings
from database import get_connection


# Version del prompt. Cualquier cambio debe incrementar esto
# para que CI valide los casos fijos.
PROMPT_VERSION = "v2.0.0"


SYSTEM_PROMPT = """Eres un analista contextual experto en CANTINAS TRADICIONALES de Ciudad de Mexico. \
Asistes al gerente de Cantina La Castellana (fundada en 1892, ubicada en Antonio Caso 58, Col. San Rafael).

TU ROL: Para cada fecha objetivo, identificas factores CONOCIDOS Y ESTABLES que afectan la \
demanda del bar. Usas exclusivamente conocimiento factico y verificable de tu entrenamiento; \
NUNCA inventas eventos especificos ni datos en tiempo real.

LO QUE SI DEBES REPORTAR (conocimiento estatico):
1. FESTIVIDADES FIJAS del calendario mexicano y su impacto cultural en cantinas:
   - San Valentin, Dia de la Madre, Dia del Padre, Dia de Muertos, etc.
   - Conmemoraciones civicas (Constitucion, Independencia, Revolucion)
   - Festividades religiosas (Reyes, Virgen Guadalupe, Navidad, Semana Santa)
2. PATRONES CULTURALES por dia de la semana:
   - Lunes/Martes: dia tranquilo, recuperacion del fin de semana
   - Jueves: ascenso de afluencia ("juernes")
   - Viernes-Sabado: pico de la semana
   - Domingo: comida familiar, cierre temprano
3. TRANSICIONES MENSUALES Y ESTACIONALIDAD:
   - Quincena/dia de pago: alza en consumo premium
   - Mes de aguinaldos (diciembre)
   - Cuaresma (impacto en consumo de licor)
   - Temporada de lluvias en CDMX (mayo-octubre): cambia tipo de tragos preferidos
4. TIPOS DE TRAGO favorecidos por contexto cultural:
   - Cocteles dulces y romanticos en San Valentin
   - Tequilas premium en fiestas patrias
   - Cervezas frias en calor; cafe-licor en frio
   - Vinos rojos y digestivos en cenas familiares

LO QUE NO DEBES HACER:
- NO inventes partidos deportivos especificos, marchas, o eventos en tiempo real
- NO sugieras pronostico de clima exacto (solo patrones estacionales generales)
- NO modifiques las predicciones del modelo de ML
- NO recomiendes cifras de stock

Formato de salida ESTRICTO (JSON):
{
  "alertas": [
    {
      "tipo": "festividad|patron_semanal|estacionalidad|cultural",
      "descripcion": "Frase clara y especifica sobre el impacto esperado.",
      "impacto_estimado": "alto|medio|bajo",
      "fuente": "Calendario civico mexicano | Patron operativo de cantina | Conocimiento estacional CDMX"
    }
  ],
  "recomendacion_gerente": "Texto breve (1-3 frases) accionable. NO modificar prediccion del modelo."
}

Si el dia es realmente neutro (sin festividad, dia de semana ordinario, ningun patron especial), \
retorna 1-2 alertas tipo "patron_semanal" o "estacionalidad" con impacto_estimado="bajo", para \
que el gerente siempre tenga contexto util en pantalla."""


USER_PROMPT_TEMPLATE = """Fecha objetivo: {fecha} ({dia_semana})
Mes: {mes_nombre} ({mes_numero}/12)
Estacion CDMX: {estacion}

CONTEXTO PRE-IDENTIFICADO (factual, no inventes alrededor de esto):
- Es fin de semana: {es_fdsem}
- Es quincena (dia de pago): {es_quincena}
- Es festivo nacional registrado: {es_festivo}
- Festividades culturales del dia: {festividades}
- Es Cuaresma (aprox): {es_cuaresma}
- Es temporada de aguinaldos (dic): {es_aguinaldo}

Genera las alertas contextuales en JSON segun el formato estricto del system prompt. \
Si las festividades culturales pre-identificadas no estan vacias, OBLIGATORIAMENTE \
emite al menos una alerta sobre ellas explicando su impacto en una cantina tradicional \
mexicana. Si la lista esta vacia, emite alertas sobre el patron semanal y la \
estacionalidad para que el gerente siempre vea contexto util."""


# ============================================================
# Festividades / contexto server-side
# ============================================================
def _detectar_festividades_culturales(fecha: date) -> list[str]:
    """
    Lista de festividades culturales relevantes para una cantina mexicana.
    Mas amplia que solo dias festivos nacionales: incluye fechas con
    impacto comercial conocido aunque no sean asueto.
    """
    m, d = fecha.month, fecha.day
    festividades: list[str] = []

    fijas = {
        (1, 1):   "Ano Nuevo",
        (1, 6):   "Dia de Reyes (Rosca)",
        (2, 2):   "Dia de la Candelaria (Tamales)",
        (2, 5):   "Aniversario Constitucion Mexicana",
        (2, 14):  "San Valentin (Dia del Amor y la Amistad)",
        (3, 8):   "Dia Internacional de la Mujer",
        (3, 21):  "Natalicio de Benito Juarez",
        (4, 30):  "Dia del Nino",
        (5, 1):   "Dia del Trabajo",
        (5, 5):   "Batalla de Puebla",
        (5, 10):  "Dia de la Madre",
        (5, 15):  "Dia del Maestro",
        (6, 16):  "Dia del Padre (aprox.)",
        (9, 13):  "Aniversario Ninos Heroes",
        (9, 15):  "Grito de Independencia (Noche Mexicana)",
        (9, 16):  "Dia de la Independencia",
        (10, 12): "Dia de la Raza/Hispanidad",
        (10, 31): "Halloween",
        (11, 1):  "Dia de Todos Santos",
        (11, 2):  "Dia de Muertos",
        (11, 20): "Revolucion Mexicana",
        (12, 12): "Dia de la Virgen de Guadalupe",
        (12, 16): "Inicio de Posadas",
        (12, 24): "Nochebuena",
        (12, 25): "Navidad",
        (12, 28): "Dia de los Santos Inocentes",
        (12, 31): "Fin de Ano",
    }
    if (m, d) in fijas:
        festividades.append(fijas[(m, d)])

    if m == 12 and 16 <= d <= 24:
        if (12, d) not in fijas:
            festividades.append("Posadas (temporada)")

    return festividades


def _detectar_estacion(fecha: date) -> str:
    """Estacion del ano simplificada para CDMX (hemisferio norte)."""
    m = fecha.month
    if m in (12, 1, 2):
        return "Invierno (seco-frio)"
    if m in (3, 4):
        return "Primavera (seco-calido)"
    if m in (5, 6, 7, 8, 9, 10):
        return "Temporada de lluvias (calor humedo)"
    return "Otono (seco-fresco)"


def _es_cuaresma(fecha: date) -> bool:
    """
    Aproximacion de Cuaresma: rango simple feb 15 - abr 15.
    Aproximacion conservadora; no se usa para guardrails.
    """
    m, d = fecha.month, fecha.day
    if m == 2 and d >= 15: return True
    if m == 3: return True
    if m == 4 and d <= 15: return True
    return False


def _es_aguinaldo(fecha: date) -> bool:
    return fecha.month == 12 and fecha.day >= 15


MESES_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

DIAS_ES = {
    0: "Lunes", 1: "Martes", 2: "Miercoles", 3: "Jueves",
    4: "Viernes", 5: "Sabado", 6: "Domingo",
}


# ============================================================
# Fallback
# ============================================================
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


# ============================================================
# Funcion principal
# ============================================================
def obtener_alertas_contextuales(
    fecha_objetivo: date,
    es_festivo: bool,
    es_quincena: bool,
    es_fin_de_semana: bool,
) -> dict:
    """
    Llama a OpenAI con el prompt versionado. Fallback silencioso si falla.
    Persiste cada respuesta en BD para auditoria (M4 Sec 3.6).
    """
    settings = get_settings()
    fecha_str = fecha_objetivo.isoformat()

    # 1. Cache hit
    with get_connection() as conn:
        row = conn.execute(
            "SELECT raw_response FROM llm_alerts WHERE alert_date = ? AND prompt_version = ?",
            (fecha_str, PROMPT_VERSION),
        ).fetchone()
        if row:
            try:
                return json.loads(row["raw_response"])
            except json.JSONDecodeError:
                pass

    # 2. Sin API key
    if not settings.OPENAI_API_KEY:
        result = _fallback_alertas()
        _persistir_alerta(fecha_str, json.dumps(result), [], fallback=True)
        return result

    # 3. OpenAI
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT)

        # Enriquecer contexto SERVER-SIDE (lo critico del fix)
        festividades = _detectar_festividades_culturales(fecha_objetivo)
        festividades_str = (
            ", ".join(festividades) if festividades else "(ninguna identificada)"
        )
        estacion = _detectar_estacion(fecha_objetivo)
        es_cuaresma = _es_cuaresma(fecha_objetivo)
        es_aguinaldo = _es_aguinaldo(fecha_objetivo)

        user_prompt = USER_PROMPT_TEMPLATE.format(
            fecha=fecha_str,
            dia_semana=DIAS_ES[fecha_objetivo.weekday()],
            mes_nombre=MESES_ES[fecha_objetivo.month],
            mes_numero=fecha_objetivo.month,
            estacion=estacion,
            es_fdsem="Si" if es_fin_de_semana else "No",
            es_quincena="Si" if es_quincena else "No",
            es_festivo="Si" if es_festivo else "No",
            festividades=festividades_str,
            es_cuaresma="Si" if es_cuaresma else "No",
            es_aguinaldo="Si" if es_aguinaldo else "No",
        )

        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        parsed["fallback_used"] = False

        fuentes_meta = [
            "festividades_pre_identificadas: " + festividades_str,
            "estacion: " + estacion,
            "es_cuaresma: " + ("Si" if es_cuaresma else "No"),
            "prompt_version: " + PROMPT_VERSION,
        ]

        _persistir_alerta(fecha_str, content, fuentes_meta, fallback=False)
        return parsed

    except Exception as e:
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


# ============================================================
# Renderizado para PDF
# ============================================================
def formatear_alertas_para_pdf(alertas_dict: dict) -> str:
    """
    Convierte el dict del LLM a texto para insertar en el PDF.
    Si no hay alertas, retorna solo la recomendacion.
    """
    alertas = alertas_dict.get("alertas", [])
    rec = alertas_dict.get("recomendacion_gerente", "")
    fallback = alertas_dict.get("fallback_used", False)

    if fallback:
        return rec

    if not alertas:
        return rec or "Dia sin eventos contextuales relevantes."

    lineas = []
    for a in alertas:
        tipo = a.get("tipo", "").replace("_", " ")
        descripcion = a.get("descripcion", "")
        if tipo:
            lineas.append(f"<b>{tipo.upper()}</b> — {descripcion}")
        else:
            lineas.append(descripcion)

    if rec:
        lineas.append("")
        lineas.append(f"<i>Recomendación:</i> {rec}")

    return "\n".join(lineas)
