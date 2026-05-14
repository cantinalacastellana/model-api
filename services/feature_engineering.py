# ============================================================
# services/feature_engineering.py
# ------------------------------------------------------------
# Pipeline de 32 features documentado en la Seccion 1 del M3.
# Familias:
#   - Banderas del Freeze (Es_Quincena, Fin_De_Semana, Es_Dia_Especial)
#   - Temporales ciclicas (Mes, Dia_Mes, Semana_Año, Trimestre + sin/cos)
#   - Autorregresivas (lags 1/7/14/28, MA 3/7/14/28, std/max/min 7, diffs)
#   - Encodings solo-train (SKU, Categoria, SKU x DiaSemana)
#
# CONTROL ANTI-LEAKAGE: todos los lags y MAs usan .shift(1) agrupado
# por SKU. Los encodings se ajustan solo sobre train y se aplican por
# mapa al resto.
# ============================================================

from datetime import date
import pandas as pd
import numpy as np
from typing import Sequence


# Lista oficial de festivos del Freeze (Seccion 10 del cuaderno).
# Se extiende dinamicamente en _es_dia_especial() para fechas futuras.
FESTIVOS_BASE = [
    "2024-02-05", "2024-02-14", "2024-03-18", "2024-03-28", "2024-03-29",
    "2024-05-01", "2024-05-10", "2024-06-16", "2024-09-15", "2024-10-01",
    "2024-10-31", "2024-11-01", "2024-11-02", "2024-11-15", "2024-11-16",
    "2024-11-17", "2024-11-18", "2024-12-12", "2024-12-31",
    "2025-02-03", "2025-02-14", "2025-03-17", "2025-04-17", "2025-04-18",
    "2025-05-01", "2025-05-10", "2025-06-15", "2025-09-15", "2025-10-31",
    "2025-11-01", "2025-11-02", "2025-11-14", "2025-11-15", "2025-11-16",
    "2025-11-17", "2025-12-12", "2025-12-31",
    "2026-02-02", "2026-02-14", "2026-03-16", "2026-04-02", "2026-04-03",
    "2026-05-01", "2026-05-10", "2026-06-21", "2026-09-15", "2026-10-31",
    "2026-11-01", "2026-11-02", "2026-11-16", "2026-12-12", "2026-12-31",
]


def es_dia_especial(fecha: date | str) -> bool:
    """Determina si una fecha es festivo segun el calendario del Freeze."""
    if isinstance(fecha, str):
        return fecha in FESTIVOS_BASE
    return fecha.isoformat() in FESTIVOS_BASE


def es_quincena(fecha: date) -> bool:
    """Pago quincenal en Mexico: dia 15 o ultimo del mes."""
    if fecha.day == 15:
        return True
    # Ultimo dia del mes
    siguiente = fecha.replace(day=28) + pd.Timedelta(days=4)
    ultimo_dia = (siguiente - pd.Timedelta(days=siguiente.day)).day
    return fecha.day == ultimo_dia


def construir_features(
    df: pd.DataFrame,
    fecha_corte_train: pd.Timestamp,
) -> pd.DataFrame:
    """
    Construye las 32 features sobre un DataFrame ya agregado a nivel
    (Fecha, SKU). Aplica controles anti-leakage.

    Args:
        df: DataFrame con columnas minimas:
            ['Fecha','SKU_Operativo','Categoria','Venta_Real',
             'Factor_Impacto_Total']
        fecha_corte_train: limite superior de train (los encodings y
            clipping solo se ajustan con datos <= esta fecha)

    Returns:
        DataFrame extendido con las 32 features + 'Venta_Real_Clipped'.
    """
    df = df.copy()
    df["Fecha"] = pd.to_datetime(df["Fecha"])
    df = df.sort_values(["SKU_Operativo", "Fecha"]).reset_index(drop=True)

    # ----- Banderas del Freeze -----
    df["Es_Quincena"] = df["Fecha"].apply(lambda d: int(es_quincena(d.date())))
    df["Fin_De_Semana"] = (df["Fecha"].dt.dayofweek >= 5).astype(int)
    df["Es_Dia_Especial"] = df["Fecha"].apply(
        lambda d: int(es_dia_especial(d.date()))
    )

    # ----- Clipping anti-outliers (SOLO con train, p95 por categoria) -----
    train_mask = df["Fecha"] <= fecha_corte_train
    limites = (
        df[train_mask].groupby("Categoria")["Venta_Real"].quantile(0.95).to_dict()
    )
    df["Venta_Real_Clipped"] = df.apply(
        lambda r: min(r["Venta_Real"], limites.get(r["Categoria"], r["Venta_Real"])),
        axis=1,
    )

    # ----- Temporales ciclicas -----
    df["Mes"] = df["Fecha"].dt.month
    df["Dia_Mes"] = df["Fecha"].dt.day
    df["Semana_Ano"] = df["Fecha"].dt.isocalendar().week.astype(int)
    df["Trimestre"] = df["Fecha"].dt.quarter
    df["Mes_sin"] = np.sin(2 * np.pi * df["Mes"] / 12)
    df["Mes_cos"] = np.cos(2 * np.pi * df["Mes"] / 12)
    df["Sem_sin"] = np.sin(2 * np.pi * df["Semana_Ano"] / 52)
    df["Sem_cos"] = np.cos(2 * np.pi * df["Semana_Ano"] / 52)
    df["DiaSem_sin"] = np.sin(2 * np.pi * df["Fecha"].dt.dayofweek / 7)
    df["DiaSem_cos"] = np.cos(2 * np.pi * df["Fecha"].dt.dayofweek / 7)

    # ----- Autorregresivas (lags + MA + std/max/min + diffs) -----
    g = df.groupby("SKU_Operativo")["Venta_Real_Clipped"]
    for lag in (1, 7, 14, 28):
        df[f"Lag_{lag}"] = g.shift(lag)
    for win in (3, 7, 14, 28):
        df[f"MA_{win}"] = g.shift(1).rolling(win, min_periods=1).mean()
    df["Std_7"] = g.shift(1).rolling(7, min_periods=1).std().fillna(0)
    df["Max_7"] = g.shift(1).rolling(7, min_periods=1).max()
    df["Min_7"] = g.shift(1).rolling(7, min_periods=1).min()
    df["Lag_Diff_1_7"] = df["Lag_1"] - df["Lag_7"]
    df["Lag_Diff_7_14"] = df["Lag_7"] - df["Lag_14"]

    # ----- Encodings solo-train -----
    # SKU factorize
    sku_map = {
        sku: i
        for i, sku in enumerate(df[train_mask]["SKU_Operativo"].unique())
    }
    df["SKU_Enc"] = df["SKU_Operativo"].map(sku_map).fillna(-1).astype(int)

    cat_map = {
        cat: i
        for i, cat in enumerate(df[train_mask]["Categoria"].unique())
    }
    df["Cat_Enc"] = df["Categoria"].map(cat_map).fillna(-1).astype(int)

    # Target encoding por SKU (media en train)
    te_sku = (
        df[train_mask].groupby("SKU_Operativo")["Venta_Real_Clipped"].mean().to_dict()
    )
    df["TE_SKU"] = df["SKU_Operativo"].map(te_sku).fillna(0)

    # Target encoding por Categoria
    te_cat = df[train_mask].groupby("Categoria")["Venta_Real_Clipped"].mean().to_dict()
    df["TE_Cat"] = df["Categoria"].map(te_cat).fillna(0)

    # Target encoding por SKU x Dia de la semana
    df["_DiaSem"] = df["Fecha"].dt.dayofweek
    te_sku_dia = (
        df[train_mask]
        .groupby(["SKU_Operativo", "_DiaSem"])["Venta_Real_Clipped"]
        .mean()
        .to_dict()
    )
    df["TE_SKU_Dia"] = df.apply(
        lambda r: te_sku_dia.get((r["SKU_Operativo"], r["_DiaSem"]), 0),
        axis=1,
    )
    df.drop(columns=["_DiaSem"], inplace=True)

    return df


# Lista oficial de columnas que entran al modelo (en el mismo orden
# que se entreno el lgbm_v1.joblib del artefacto M3).
FEATURE_COLUMNS = [
    "Es_Quincena", "Fin_De_Semana", "Es_Dia_Especial",
    "Mes", "Dia_Mes", "Semana_Ano", "Trimestre",
    "Mes_sin", "Mes_cos", "Sem_sin", "Sem_cos", "DiaSem_sin", "DiaSem_cos",
    "Lag_1", "Lag_7", "Lag_14", "Lag_28",
    "MA_3", "MA_7", "MA_14", "MA_28",
    "Std_7", "Max_7", "Min_7",
    "Lag_Diff_1_7", "Lag_Diff_7_14",
    "SKU_Enc", "Cat_Enc",
    "TE_SKU", "TE_Cat", "TE_SKU_Dia",
    "Factor_Impacto_Total",
]
