# ============================================================
# tests/test_drift.py - Tests del modulo de drift
# ============================================================

import pandas as pd
import numpy as np

from services.drift_service import (
    chequear_data_drift, chequear_model_drift,
    calcular_wape_ponderado, calcular_wape_simple,
)


def test_data_drift_distribuciones_iguales_no_alerta():
    rng = np.random.RandomState(42)
    serie_a = pd.Series(rng.normal(10, 2, 100))
    serie_b = pd.Series(rng.normal(10, 2, 100))
    r = chequear_data_drift(serie_a, serie_b)
    assert r["alert"] is False
    assert r["p_value"] > 0.01


def test_data_drift_distribuciones_distintas_alerta():
    rng = np.random.RandomState(42)
    serie_a = pd.Series(rng.normal(10, 2, 200))
    serie_b = pd.Series(rng.normal(20, 2, 200))  # corrida
    r = chequear_data_drift(serie_a, serie_b)
    assert r["alert"] is True
    assert r["p_value"] < 0.01


def test_data_drift_insuficientes_datos_no_alerta():
    r = chequear_data_drift(pd.Series([1, 2]), pd.Series([3, 4]))
    assert r["alert"] is False
    assert "Insuficientes datos" in r["reason"]


def test_model_drift_ratio_alto_alerta():
    # WAPE observado 150% del esperado -> ratio 1.5 -> alerta
    r = chequear_model_drift(wape_observado=150, wape_esperado=100)
    assert r["alert"] is True
    assert r["ratio"] == 1.5


def test_model_drift_ratio_aceptable_no_alerta():
    r = chequear_model_drift(wape_observado=105, wape_esperado=100)
    assert r["alert"] is False
    assert r["ratio"] == 1.05


def test_wape_simple_perfecto_es_cero():
    df = pd.DataFrame({
        "pred_final": [10, 20, 30],
        "Venta_Real": [10, 20, 30],
        "Factor_Impacto_Total": [1, 1, 1],
    })
    assert calcular_wape_simple(df) == 0.0


def test_wape_ponderado_con_factores():
    df = pd.DataFrame({
        "pred_final": [10, 20],
        "Venta_Real": [12, 18],  # errores: 2 y 2
        "Factor_Impacto_Total": [1, 2],  # segundo tiene mas peso
    })
    # (|10-12|*1 + |20-18|*2) / (12+18) * 100 = (2+4)/30*100 = 20.0
    assert calcular_wape_ponderado(df) == 20.0


def test_wape_sin_ventas_es_cero():
    df = pd.DataFrame({
        "pred_final": [], "Venta_Real": [], "Factor_Impacto_Total": [],
    })
    assert calcular_wape_ponderado(df) == 0.0
