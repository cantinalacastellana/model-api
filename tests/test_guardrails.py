# ============================================================
# tests/test_guardrails.py - Tests de GR1-GR4
# ------------------------------------------------------------
# Preserva la logica de los tests del artefacto M3. Estos son los
# BASELINE TESTS pedidos por el CI minimo de Tier A.
# ============================================================

import pandas as pd
import pytest

from services.guardrails import aplicar_guardrails, resumir_guardrails


def _hacer_df(skus_preds):
    """Helper: construye un DF con sku/categoria/pred_raw."""
    return pd.DataFrame([
        {"sku": s, "categoria": "Test", "pred_raw": p}
        for s, p in skus_preds
    ])


# ------------------------------------------------------------
# GR1 - Dias especiales (+30%)
# ------------------------------------------------------------
def test_gr1_aplica_factor_en_dia_especial():
    df = _hacer_df([("SKU_A (Sencillo)", 10.0)])
    historia = {"SKU_A (Sencillo)": {
        "dias_historia": 365, "max_historico": 100,
        "freq_ultimos_30d": 1.0, "baseline_pm4w": 10,
    }}
    r = aplicar_guardrails(df, es_dia_especial=True, historia_por_sku=historia)
    # 10 * 1.30 = 13
    assert int(r.iloc[0]["pred_final"]) == 13
    assert "GR1" in r.iloc[0]["guardrails_applied"]


def test_gr1_no_aplica_en_dia_normal():
    df = _hacer_df([("SKU_A (Sencillo)", 10.0)])
    historia = {"SKU_A (Sencillo)": {
        "dias_historia": 365, "max_historico": 100,
        "freq_ultimos_30d": 1.0, "baseline_pm4w": 10,
    }}
    r = aplicar_guardrails(df, es_dia_especial=False, historia_por_sku=historia)
    assert int(r.iloc[0]["pred_final"]) == 10
    assert "GR1" not in r.iloc[0]["guardrails_applied"]


# ------------------------------------------------------------
# GR2 - Cap 2x max historico
# ------------------------------------------------------------
def test_gr2_trunca_outliers():
    # Prediccion = 250, max historico = 100, cap = 200
    df = _hacer_df([("SKU_A (Sencillo)", 250.0)])
    historia = {"SKU_A (Sencillo)": {
        "dias_historia": 365, "max_historico": 100,
        "freq_ultimos_30d": 1.0, "baseline_pm4w": 10,
    }}
    r = aplicar_guardrails(df, es_dia_especial=False, historia_por_sku=historia)
    assert int(r.iloc[0]["pred_final"]) == 200
    assert bool(r.iloc[0]["truncated"]) is True
    assert "GR2" in r.iloc[0]["guardrails_applied"]


def test_gr2_no_trunca_dentro_de_rango():
    df = _hacer_df([("SKU_A (Sencillo)", 50.0)])
    historia = {"SKU_A (Sencillo)": {
        "dias_historia": 365, "max_historico": 100,
        "freq_ultimos_30d": 1.0, "baseline_pm4w": 10,
    }}
    r = aplicar_guardrails(df, es_dia_especial=False, historia_por_sku=historia)
    assert int(r.iloc[0]["pred_final"]) == 50
    assert bool(r.iloc[0]["truncated"]) is False


# ------------------------------------------------------------
# GR3 - Cold start (<30 dias historia)
# ------------------------------------------------------------
def test_gr3_cold_start_usa_baseline():
    df = _hacer_df([("SKU_NUEVO (Sencillo)", 100.0)])
    historia = {"SKU_NUEVO (Sencillo)": {
        "dias_historia": 10,  # Cold start
        "max_historico": 5, "freq_ultimos_30d": 0.5,
        "baseline_pm4w": 3,
    }}
    r = aplicar_guardrails(df, es_dia_especial=False, historia_por_sku=historia)
    assert int(r.iloc[0]["pred_final"]) == 3
    assert bool(r.iloc[0]["cold_start"]) is True
    assert "GR3" in r.iloc[0]["guardrails_applied"]


def test_gr3_cold_start_no_aplica_otros_guardrails():
    """En cold start, NO se acumulan GR1/GR2/GR4."""
    df = _hacer_df([("SKU_NUEVO (Sencillo)", 100.0)])
    historia = {"SKU_NUEVO (Sencillo)": {
        "dias_historia": 5, "max_historico": 5,
        "freq_ultimos_30d": 0.1, "baseline_pm4w": 3,
    }}
    r = aplicar_guardrails(df, es_dia_especial=True, historia_por_sku=historia)
    aplicados = r.iloc[0]["guardrails_applied"]
    assert aplicados == ["GR3"]
    assert int(r.iloc[0]["pred_final"]) == 3


# ------------------------------------------------------------
# GR4 - Long-tail supresion
# ------------------------------------------------------------
def test_gr4_suprime_long_tail():
    df = _hacer_df([("SKU_RARO (Sencillo)", 2.0)])
    historia = {"SKU_RARO (Sencillo)": {
        "dias_historia": 365, "max_historico": 5,
        "freq_ultimos_30d": 0.1,  # < 30%
        "baseline_pm4w": 1,
    }}
    r = aplicar_guardrails(df, es_dia_especial=False, historia_por_sku=historia)
    assert int(r.iloc[0]["pred_final"]) == 0
    assert bool(r.iloc[0]["suppressed"]) is True
    assert "GR4" in r.iloc[0]["guardrails_applied"]


def test_gr4_no_suprime_si_freq_alta():
    df = _hacer_df([("SKU_FRECUENTE (Sencillo)", 2.0)])
    historia = {"SKU_FRECUENTE (Sencillo)": {
        "dias_historia": 365, "max_historico": 10,
        "freq_ultimos_30d": 0.8, "baseline_pm4w": 2,
    }}
    r = aplicar_guardrails(df, es_dia_especial=False, historia_por_sku=historia)
    assert int(r.iloc[0]["pred_final"]) == 2
    assert "GR4" not in r.iloc[0]["guardrails_applied"]


# ------------------------------------------------------------
# Resumen
# ------------------------------------------------------------
def test_resumir_cuenta_correctamente():
    df = _hacer_df([
        ("SKU_1 (Sencillo)", 10),
        ("SKU_2 (Sencillo)", 250),
        ("SKU_3 (Sencillo)", 100),
    ])
    hist = {
        "SKU_1 (Sencillo)": {
            "dias_historia": 365, "max_historico": 100,
            "freq_ultimos_30d": 1.0, "baseline_pm4w": 10,
        },
        "SKU_2 (Sencillo)": {  # GR2
            "dias_historia": 365, "max_historico": 100,
            "freq_ultimos_30d": 1.0, "baseline_pm4w": 10,
        },
        "SKU_3 (Sencillo)": {  # GR3 cold start
            "dias_historia": 5, "max_historico": 5,
            "freq_ultimos_30d": 1.0, "baseline_pm4w": 1,
        },
    }
    r = aplicar_guardrails(df, es_dia_especial=True, historia_por_sku=hist)
    resumen = resumir_guardrails(r)
    assert resumen["GR1"] == 2  # 2 SKUs activan GR1 (los no-cold-start en dia especial)
    assert resumen["GR2"] == 1
    assert resumen["GR3"] == 1
    assert resumen["GR4"] == 0
