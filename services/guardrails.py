# ============================================================
# services/guardrails.py - Guardrails GR1-GR4 del M3
# ------------------------------------------------------------
# Implementacion fiel a la tabla "Guardrails y Condiciones de no
# uso" de la Seccion 4.4 del entregable M3 + Validacion de la
# Seccion 4.4 ("Validacion Cuantitativa de los Guardrails").
#
# Aplicacion incremental (GR1 -> GR2 -> GR3 -> GR4) sobre las
# predicciones crudas del modelo.
#
# IMPORTANTE: este modulo NO depende del modelo. Recibe predicciones
# y retorna predicciones modificadas + log de guardrails aplicados
# por SKU. Cualquier modelo (LightGBM actual, futuras versiones, o
# baseline en fallback) usa el mismo set de guardrails.
# ============================================================

from datetime import date
from typing import TypedDict
import pandas as pd
import numpy as np

from config import get_settings


class GuardrailResult(TypedDict):
    """Resultado individual por SKU tras aplicar guardrails."""
    sku: str
    pred_raw: float
    pred_final: float
    guardrails_applied: list[str]
    truncated: bool
    suppressed: bool
    cold_start: bool


def aplicar_guardrails(
    df_pred: pd.DataFrame,
    es_dia_especial: bool,
    historia_por_sku: dict[str, dict],
) -> pd.DataFrame:
    """
    Aplica los 4 guardrails incrementalmente.

    Args:
        df_pred: DataFrame con columnas ['sku', 'pred_raw', 'categoria']
        es_dia_especial: True si la fecha objetivo cae en festivo
        historia_por_sku: dict con info historica de cada SKU:
            {
                "SKU_X": {
                    "dias_historia": 365,
                    "max_historico": 80,
                    "freq_ultimos_30d": 0.85,
                    "baseline_pm4w": 12.0,
                    "es_nuevo": False,
                }
            }

    Returns:
        DataFrame original + columnas ['pred_final', 'guardrails_applied',
        'truncated', 'suppressed', 'cold_start']
    """
    settings = get_settings()
    df = df_pred.copy()

    # Inicializar columnas de seguimiento
    df["pred_final"] = df["pred_raw"].astype(float)
    df["guardrails_applied"] = [[] for _ in range(len(df))]
    df["truncated"] = False
    df["suppressed"] = False
    df["cold_start"] = False

    for idx, row in df.iterrows():
        sku = row["sku"]
        info = historia_por_sku.get(sku, {})
        aplicados: list[str] = []
        pred = float(row["pred_raw"])

        # ---------------------------------------------------
        # GR3: COLD START (prioritario - fallback completo)
        # Si SKU tiene <30 dias de historia, fallback a baseline
        # PM 4 semanas; no aplican GR1, GR2 ni GR4 (no hay base
        # historica confiable).
        # ---------------------------------------------------
        dias_historia = info.get("dias_historia", 0)
        if dias_historia < settings.GR3_DIAS_COLD_START:
            baseline = info.get("baseline_pm4w", 1.0)
            pred = float(baseline)
            df.at[idx, "cold_start"] = True
            aplicados.append("GR3")
            df.at[idx, "pred_final"] = pred
            df.at[idx, "guardrails_applied"] = aplicados
            continue  # no procesar otros guardrails

        # ---------------------------------------------------
        # GR1: DIAS ESPECIALES
        # Mitigacion +30% en festivos para reducir subestimacion
        # (caso San Valentin 2026 en TEST).
        # ---------------------------------------------------
        if es_dia_especial:
            pred = pred * settings.GR1_FACTOR_DIA_ESPECIAL
            aplicados.append("GR1")

        # ---------------------------------------------------
        # GR2: CAP AL 2x MAX HISTORICO
        # Captura errores de captura del POS y eventos
        # extraordinarios. Trunca y marca con flag.
        # ---------------------------------------------------
        max_hist = info.get("max_historico", 0)
        if max_hist > 0:
            cap = settings.GR2_FACTOR_CAP_OUTLIER * max_hist
            if pred > cap:
                pred = cap
                df.at[idx, "truncated"] = True
                aplicados.append("GR2")

        # ---------------------------------------------------
        # GR4: LONG-TAIL SUPRESION
        # Si SKU tiene freq <30% en ultimos 30d Y prediccion <=2,
        # suprimir a 0 (cubre el "surtir 1 de cada uno" del PDF).
        # ---------------------------------------------------
        freq = info.get("freq_ultimos_30d", 1.0)
        if freq < settings.GR4_FREQ_LONG_TAIL and pred <= settings.GR4_PRED_LONG_TAIL:
            pred = 0.0
            df.at[idx, "suppressed"] = True
            aplicados.append("GR4")

        # Aplicar redondeo no-negativo final (del clip_round del M3)
        pred = max(0, round(pred))

        df.at[idx, "pred_final"] = pred
        df.at[idx, "guardrails_applied"] = aplicados

    return df


def resumir_guardrails(df_con_guardrails: pd.DataFrame) -> dict[str, int]:
    """
    Cuenta cuantos SKUs activaron cada guardrail. Util para el log
    estructurado del dia y para mostrar en el frontend.
    """
    counts = {"GR1": 0, "GR2": 0, "GR3": 0, "GR4": 0}
    for lst in df_con_guardrails["guardrails_applied"]:
        for gr in lst:
            if gr in counts:
                counts[gr] += 1
    return counts
