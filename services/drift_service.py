# ============================================================
# services/drift_service.py
# ------------------------------------------------------------
# Implementacion del monitoreo descrito en la Seccion 6 del M3
# (Monitoreo minimo CI/CD):
#
#   Data drift  -> Test KS sobre Venta_Real semanal vs baseline
#                  p<0.01 = alerta
#   Model drift -> ratio WAPE_semanal_observado / WAPE_esperado
#                  ratio >= 1.20 (degradacion >=20%) = alerta
#   Pipeline    -> tasa de ejecuciones exitosas (>=95%)
#
# IMPORTANTE: este modulo NUNCA dispara reentrene. Solo escribe
# alertas en la tabla drift_checks. El admin (data scientist) ve
# la alerta en el frontend y decide manualmente si invoca el
# endpoint /admin/retrain.
# ============================================================

from __future__ import annotations
from datetime import date, datetime, timedelta
import json
import pandas as pd
import numpy as np
from scipy import stats

from config import get_settings
from database import get_connection


def calcular_wape_ponderado(
    df: pd.DataFrame, col_pred: str = "pred_final", col_real: str = "Venta_Real"
) -> float:
    """WAPE Ponderado Logistico del Freeze."""
    if col_real not in df.columns or len(df) == 0:
        return 0.0
    y = df[col_real].values
    yhat = df[col_pred].values
    factor = df.get("Factor_Impacto_Total", pd.Series([1.0] * len(df))).values
    s = y.sum()
    if s == 0:
        return 0.0
    return float((np.abs(y - yhat) * factor).sum() / s * 100)


def calcular_wape_simple(
    df: pd.DataFrame, col_pred: str = "pred_final", col_real: str = "Venta_Real"
) -> float:
    """WAPE Simple."""
    if col_real not in df.columns or len(df) == 0:
        return 0.0
    y = df[col_real].values
    yhat = df[col_pred].values
    s = y.sum()
    if s == 0:
        return 0.0
    return float(np.abs(y - yhat).sum() / s * 100)


def chequear_data_drift(
    ventas_recientes: pd.Series, ventas_baseline: pd.Series
) -> dict:
    """
    Test Kolmogorov-Smirnov entre distribuciones recientes y baseline.
    Retorna dict con ks_statistic, p_value y alerta.
    """
    if len(ventas_recientes) < 10 or len(ventas_baseline) < 10:
        return {
            "ks_statistic": None,
            "p_value": None,
            "alert": False,
            "reason": "Insuficientes datos para KS test",
        }
    ks_stat, p_value = stats.ks_2samp(ventas_recientes, ventas_baseline)
    settings = get_settings()
    alert = bool(p_value < settings.UMBRAL_DRIFT_DATA_PVALUE)
    return {
        "ks_statistic": float(ks_stat),
        "p_value": float(p_value),
        "alert": alert,
        "reason": (
            f"p={p_value:.4f} < {settings.UMBRAL_DRIFT_DATA_PVALUE} (distribucion cambio)"
            if alert else f"p={p_value:.4f}, sin drift"
        ),
    }


def chequear_model_drift(wape_observado: float, wape_esperado: float) -> dict:
    """
    Compara WAPE observado (semana en operacion) vs esperado (validacion).
    Si ratio >= 1.20 (degradacion >=20%), dispara alerta.
    """
    if wape_esperado <= 0:
        return {
            "ratio": None,
            "alert": False,
            "reason": "WAPE esperado invalido",
        }
    settings = get_settings()
    ratio = wape_observado / wape_esperado
    alert = bool(ratio >= settings.UMBRAL_DRIFT_MODEL_RATIO)
    return {
        "ratio": float(ratio),
        "alert": alert,
        "reason": (
            f"WAPE observado {wape_observado:.2f}% es {ratio:.2f}x el esperado "
            f"{wape_esperado:.2f}% (umbral {settings.UMBRAL_DRIFT_MODEL_RATIO})"
            if alert else f"WAPE en rango ({ratio:.2f}x)"
        ),
    }


def chequear_pipeline_health(ultimas_n: int = 14) -> dict:
    """
    Tasa de ejecuciones exitosas de los ultimos N dias (predicciones
    completadas / dias). Si < 95%, alerta.
    """
    settings = get_settings()
    desde = (datetime.now().date() - timedelta(days=ultimas_n)).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT prediction_date) AS dias_con_pred
            FROM predictions
            WHERE prediction_date >= ?
            """,
            (desde,),
        ).fetchone()
    dias_con_pred = row["dias_con_pred"] or 0
    health = dias_con_pred / ultimas_n if ultimas_n > 0 else 0.0
    alert = health < settings.UMBRAL_PIPELINE_HEALTH
    return {
        "health": float(health),
        "dias_con_pred": dias_con_pred,
        "ventana_dias": ultimas_n,
        "alert": alert,
        "reason": (
            f"Solo {dias_con_pred}/{ultimas_n} dias con prediccion (<95%)"
            if alert else f"{dias_con_pred}/{ultimas_n} dias OK"
        ),
    }


def ejecutar_chequeo_drift_completo(
    df_ventas_recientes: pd.DataFrame | None = None,
    wape_esperado: float = 109.66,  # WAPE Ponderado VAL del M3
) -> dict:
    """
    Corre los tres chequeos y persiste el resultado en drift_checks.
    Retorna el dict completo. NO dispara reentrene.

    df_ventas_recientes: DataFrame opcional con ventas recientes para
        comparar contra baseline historica (de la BD actual_sales si
        no se pasa).
    """
    settings = get_settings()
    hoy = datetime.now().date()
    fecha_str = hoy.isoformat()

    # ----- Data drift: comparar ventas ultimos 14d vs baseline historico -----
    if df_ventas_recientes is None:
        # Cargar de actual_sales en BD
        with get_connection() as conn:
            desde_recientes = (hoy - timedelta(days=14)).isoformat()
            desde_baseline = (hoy - timedelta(days=90)).isoformat()
            recientes = pd.read_sql_query(
                "SELECT units_sold FROM actual_sales WHERE sale_date >= ?",
                conn,
                params=[desde_recientes],
            )
            baseline = pd.read_sql_query(
                """SELECT units_sold FROM actual_sales
                   WHERE sale_date < ? AND sale_date >= ?""",
                conn,
                params=[desde_recientes, desde_baseline],
            )
        data_drift = chequear_data_drift(
            recientes["units_sold"] if len(recientes) > 0 else pd.Series([]),
            baseline["units_sold"] if len(baseline) > 0 else pd.Series([]),
        )
    else:
        # Caller pasa el DF (caso de testeo o admin manual)
        data_drift = chequear_data_drift(
            df_ventas_recientes.tail(14 * 50)["Venta_Real"],
            df_ventas_recientes.iloc[: -(14 * 50)]["Venta_Real"]
            if len(df_ventas_recientes) > 14 * 50
            else pd.Series([]),
        )

    # ----- Model drift: WAPE observado vs esperado -----
    wape_observado = calcular_wape_observado_semana(hoy)
    if wape_observado is not None:
        model_drift = chequear_model_drift(wape_observado, wape_esperado)
    else:
        model_drift = {
            "ratio": None,
            "alert": False,
            "reason": "Sin ventas reales registradas en ventana reciente",
        }

    # ----- Pipeline health -----
    pipeline = chequear_pipeline_health()

    # ----- Consolidar alertas -----
    reasons = []
    if data_drift["alert"]:
        reasons.append(f"data_drift: {data_drift['reason']}")
    if model_drift["alert"]:
        reasons.append(f"model_drift: {model_drift['reason']}")
    if pipeline["alert"]:
        reasons.append(f"pipeline_health: {pipeline['reason']}")
    alert_triggered = bool(reasons)

    # Recomendacion textual para el frontend
    if alert_triggered:
        recommendation = (
            "ALERTA: Se detecto drift o degradacion. El data scientist debe "
            "revisar e invocar manualmente POST /admin/retrain si procede. "
            "Mientras tanto, el modelo actual sigue activo."
        )
    else:
        recommendation = "Modelo estable. Sin acciones requeridas."

    # Persistir
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO drift_checks (
                check_date, data_drift_ks_stat, data_drift_pvalue,
                model_drift_wape_obs, model_drift_wape_exp, model_drift_ratio,
                pipeline_health, alert_triggered, alert_reasons
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                fecha_str,
                data_drift.get("ks_statistic"),
                data_drift.get("p_value"),
                wape_observado,
                wape_esperado,
                model_drift.get("ratio"),
                pipeline.get("health"),
                int(alert_triggered),
                json.dumps(reasons, ensure_ascii=False),
            ),
        )

    return {
        "check_date": fecha_str,
        "data_drift": data_drift,
        "model_drift": model_drift,
        "pipeline_health": pipeline,
        "alert_triggered": alert_triggered,
        "alert_reasons": reasons,
        "recommendation": recommendation,
    }


def calcular_wape_observado_semana(hasta_fecha: date) -> float | None:
    """
    Calcula el WAPE Ponderado de los ultimos 7 dias, comparando
    predictions vs actual_sales registradas. Retorna None si no hay
    suficientes datos.
    """
    desde = (hasta_fecha - timedelta(days=7)).isoformat()
    hasta_str = hasta_fecha.isoformat()
    with get_connection() as conn:
        df = pd.read_sql_query(
            """
            SELECT p.prediction_date, p.sku, p.pred_final,
                   a.units_sold AS Venta_Real,
                   COALESCE(a.factor_impacto, 1.0) AS Factor_Impacto_Total
            FROM predictions p
            INNER JOIN actual_sales a
              ON a.sale_date = p.prediction_date AND a.sku = p.sku
            WHERE p.prediction_date >= ? AND p.prediction_date <= ?
            """,
            conn,
            params=[desde, hasta_str],
        )
    if len(df) < 10:
        return None
    return calcular_wape_ponderado(df)
