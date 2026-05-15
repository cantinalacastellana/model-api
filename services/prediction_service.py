# ============================================================
# services/prediction_service.py
# ------------------------------------------------------------
# Servicio principal de prediccion. Orquesta:
#   1. Carga del modelo activo (LightGBM)
#   2. Feature engineering del dataset
#   3. Prediccion cruda por SKU para la fecha objetivo
#   4. Aplicacion de guardrails (GR1-GR4)
#   5. Conversion de unidades a botellas
#   6. Calculo del refuerzo vespertino sugerido
#   7. Persistencia en BD + archivo PDF/CSV/log
#   8. Fallback a baseline PM 4 semanas si CUALQUIER paso falla
#
# El fallback es lo unico que DEBE funcionar siempre: si el modelo
# no carga, si las features fallan, o si la prediccion arroja
# nan/inf, el sistema cae al baseline historico para garantizar
# continuidad operativa (Seccion 4.4 del M3).
# ============================================================

from __future__ import annotations
from datetime import date, datetime
import json
import joblib
import pandas as pd
import numpy as np
from pathlib import Path

from config import get_settings
from database import get_connection
from services.feature_engineering import (
    construir_features, FEATURE_COLUMNS, es_dia_especial,
)
from services.guardrails import aplicar_guardrails, resumir_guardrails
from services.conversion_botellas import unidades_a_botellas


# ============================================================
# Carga del modelo activo
# ============================================================
def _extraer_modelo_predecible(obj):
    """
    Extrae el modelo con metodo .predict() de lo que devuelve joblib.load().

    Esto es necesario porque el modelo M3 entregado por Jose Emilio
    (lgbm_v1.joblib) NO se guardo como Booster/LGBMRegressor directo,
    sino como un dict de artefactos con estructura tipo:
        {
            "model": <LGBMRegressor o Booster>,
            "feature_names": [...],
            "config": {...},
            ...
        }

    En cambio, los modelos que entrena retrain_service.py se guardan
    como LGBMRegressor desnudos con joblib.dump(model, path), asi que
    tienen .predict() directo.

    Esta funcion es tolerante a ambos formatos:
      - Si el objeto cargado ya tiene .predict(), lo devuelve tal cual.
      - Si es un dict, busca el modelo en las llaves comunes.
      - Si no encuentra nada, lanza TypeError con info de debug.
    """
    # Caso 1: ya es un modelo con .predict() (LGBMRegressor, Booster, etc.)
    if hasattr(obj, "predict") and callable(obj.predict):
        return obj

    # Caso 2: es un dict de artefactos - buscar el modelo dentro
    if isinstance(obj, dict):
        # Llaves comunes en orden de prioridad
        for key in (
            "model", "modelo", "booster", "estimator",
            "regressor", "clf", "lgbm", "lgb_model", "lgbm_model",
        ):
            candidate = obj.get(key)
            if candidate is not None and hasattr(candidate, "predict") and callable(candidate.predict):
                return candidate

        # Fallback: cualquier valor del dict que tenga .predict
        for v in obj.values():
            if hasattr(v, "predict") and callable(v.predict):
                return v

        raise TypeError(
            f"El archivo cargado es un dict pero ninguna de sus llaves "
            f"contiene un objeto con .predict(). Llaves disponibles: "
            f"{list(obj.keys())}"
        )

    raise TypeError(
        f"No se pudo extraer un modelo predecible. Tipo cargado: "
        f"{type(obj).__name__}"
    )


def cargar_modelo_activo() -> dict:
    """
    Carga el modelo marcado como is_active=1 en la BD. Retorna un dict:
        {
            "version": "...",
            "model": <objeto LightGBM con .predict>,
            "path": "...",
            "wape_val": ...,
        }

    Si no hay ningun modelo activo o el archivo no existe, lanza
    FileNotFoundError. El llamador (predict_endpoint) captura esto
    y dispara el fallback a baseline.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM model_versions WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        if row is None:
            raise FileNotFoundError("No hay modelo activo registrado en BD")
        version = row["version"]
        path = Path(row["path"])
        wape_val = row["wape_val"]

    if not path.exists():
        raise FileNotFoundError(f"Archivo del modelo no existe: {path}")

    # Carga tolerante a dict de artefactos M3 o LGBMRegressor desnudo
    raw_loaded = joblib.load(path)
    model = _extraer_modelo_predecible(raw_loaded)

    return {
        "version": version,
        "model": model,
        "path": str(path),
        "wape_val": wape_val,
    }


# ============================================================
# Baseline PM 4 semanas (fallback)
# ============================================================
def predecir_baseline_pm4w(
    df_hist: pd.DataFrame,
    fecha_objetivo: pd.Timestamp,
    skus: list[str],
) -> pd.DataFrame:
    """
    Baseline del Freeze: promedio de las ultimas 4 ocurrencias del
    mismo (SKU, dia-de-semana). Mismo metodo de la Seccion 6 del
    cuaderno M3.
    """
    dow_objetivo = fecha_objetivo.dayofweek
    df_hist = df_hist[df_hist["Fecha"] < fecha_objetivo].copy()
    df_hist["dow"] = df_hist["Fecha"].dt.dayofweek
    df_hist = df_hist[df_hist["dow"] == dow_objetivo]

    preds = []
    for sku in skus:
        h = (
            df_hist[df_hist["SKU_Operativo"] == sku]
            .sort_values("Fecha")
            .tail(4)
        )
        pred = float(h["Venta_Real"].mean()) if len(h) > 0 else 0.0
        preds.append({"sku": sku, "pred_raw": pred})
    return pd.DataFrame(preds)


# ============================================================
# Calculo del refuerzo vespertino (regla operativa M3 Sec 14)
# ============================================================
def calcular_refuerzos_vespertinos(
    df_raw: pd.DataFrame, skus_prediccion: list[str]
) -> dict[str, bool]:
    """
    Para cada SKU, calcula si su venta historica en DEMANDA MEDIA
    (20:00-22:00) supera el 40% del total diario. Si si, el PDF
    sugerira un segundo viaje del corredor entre 19:00 y 19:30.

    Esta es una regla HISTORICA, NO una prediccion del modelo (claro
    en la Seccion 5 del M3).
    """
    if "Bloque_Horario" not in df_raw.columns:
        return {sku: False for sku in skus_prediccion}

    refuerzos = {}
    for sku in skus_prediccion:
        s = df_raw[df_raw["SKU_Operativo"] == sku]
        if len(s) == 0:
            refuerzos[sku] = False
            continue
        total = s["Venta_Real"].sum()
        if total == 0:
            refuerzos[sku] = False
            continue
        media = s[s["Bloque_Horario"] == "DEMANDA MEDIA"]["Venta_Real"].sum()
        refuerzos[sku] = (media / total) >= 0.40
    return refuerzos


# ============================================================
# Construir info historica para guardrails
# ============================================================
def construir_historia_por_sku(
    df_hist: pd.DataFrame, fecha_objetivo: pd.Timestamp
) -> dict[str, dict]:
    """
    Para cada SKU, calcula los atributos que necesitan los guardrails:
        - dias_historia
        - max_historico (en escala diaria)
        - freq_ultimos_30d (proporcion de dias con venta > 0)
        - baseline_pm4w (fallback para cold start)
    """
    dow_obj = fecha_objetivo.dayofweek
    historia = {}
    df_hist = df_hist[df_hist["Fecha"] < fecha_objetivo]
    ventana_30d = fecha_objetivo - pd.Timedelta(days=30)

    for sku in df_hist["SKU_Operativo"].unique():
        s = df_hist[df_hist["SKU_Operativo"] == sku]
        if len(s) == 0:
            continue
        dias_historia = (fecha_objetivo - s["Fecha"].min()).days
        max_hist = float(s["Venta_Real"].max())
        ult_30 = s[s["Fecha"] >= ventana_30d]
        if len(ult_30) > 0:
            freq = float((ult_30["Venta_Real"] > 0).mean())
        else:
            freq = 0.0
        # Baseline PM 4 semanas para este SKU/dow
        same_dow = s[s["Fecha"].dt.dayofweek == dow_obj].sort_values("Fecha").tail(4)
        baseline = float(same_dow["Venta_Real"].mean()) if len(same_dow) > 0 else 0.0

        historia[sku] = {
            "dias_historia": dias_historia,
            "max_historico": max_hist,
            "freq_ultimos_30d": freq,
            "baseline_pm4w": baseline,
            "es_nuevo": dias_historia < 30,
        }
    return historia


# ============================================================
# Funcion principal
# ============================================================
def generar_prediccion_dia(
    fecha_objetivo: date,
    df_hist: pd.DataFrame,
    user_id: int,
) -> dict:
    """
    Genera la prediccion completa para la fecha objetivo.

    Returns: dict con resumen de la prediccion + lista de items.
        Si algun paso critico falla, cae automaticamente al baseline
        y marca fallback_used=True en la respuesta.
    """
    settings = get_settings()
    fecha_ts = pd.Timestamp(fecha_objetivo)
    es_especial = es_dia_especial(fecha_objetivo)

    fallback_used = False
    fallback_reason = None
    model_version = "baseline_pm4w"
    pred_raw_df = None

    # -----------------------------------------------------------
    # 1. Cargar modelo + features + predecir (con manejo de fallas)
    # -----------------------------------------------------------
    skus = sorted(df_hist["SKU_Operativo"].unique().tolist())
    try:
        info_modelo = cargar_modelo_activo()
        model = info_modelo["model"]
        model_version = info_modelo["version"]

        # Construir features. fecha_corte_train = ayer
        df_feat = construir_features(df_hist, fecha_corte_train=fecha_ts - pd.Timedelta(days=1))

        # Quedarse solo con la fila de fecha objetivo por SKU
        # Si no existe, crearla
        df_predecir = []
        for sku in skus:
            sub = df_feat[(df_feat["SKU_Operativo"] == sku)]
            # Tomar la fila mas reciente para usar sus lags como base
            if len(sub) == 0:
                continue
            ultima = sub.sort_values("Fecha").iloc[-1].copy()
            ultima["Fecha"] = fecha_ts
            ultima["Es_Dia_Especial"] = int(es_especial)
            df_predecir.append(ultima)
        df_predecir = pd.DataFrame(df_predecir)

        # Verificar columnas requeridas
        if not all(c in df_predecir.columns for c in FEATURE_COLUMNS):
            raise ValueError("Faltan columnas en df_predecir tras feature engineering")

        # Imputar NaN con 0 (los lags al principio del SKU pueden ser NaN)
        X = df_predecir[FEATURE_COLUMNS].fillna(0).values

        preds = model.predict(X)
        # Post-proceso obligatorio: no negativos, enteros
        preds = np.clip(np.round(preds), 0, None).astype(int)

        pred_raw_df = pd.DataFrame({
            "sku": df_predecir["SKU_Operativo"].values,
            "categoria": df_predecir["Categoria"].values,
            "pred_raw": preds.astype(float),
        })

    except Exception as e:
        # Cualquier falla: fallback a baseline
        fallback_used = True
        fallback_reason = f"Modelo principal fallo: {type(e).__name__}: {str(e)[:200]}"
        pred_raw_df = predecir_baseline_pm4w(df_hist, fecha_ts, skus)
        # Reemplazar categoria
        cat_map = df_hist.groupby("SKU_Operativo")["Categoria"].first().to_dict()
        pred_raw_df["categoria"] = pred_raw_df["sku"].map(cat_map)
        model_version = "baseline_pm4w (FALLBACK)"

    # -----------------------------------------------------------
    # 2. Aplicar guardrails GR1-GR4
    # -----------------------------------------------------------
    historia = construir_historia_por_sku(df_hist, fecha_ts)
    pred_con_gr = aplicar_guardrails(pred_raw_df, es_especial, historia)

    # -----------------------------------------------------------
    # 3. Conversion a botellas
    # -----------------------------------------------------------
    pred_con_gr["bottles"] = pred_con_gr.apply(
        lambda r: unidades_a_botellas(r["pred_final"], r["sku"]),
        axis=1,
    )

    # -----------------------------------------------------------
    # 4. Refuerzos vespertinos (regla historica)
    # -----------------------------------------------------------
    refuerzos = calcular_refuerzos_vespertinos(df_hist, pred_con_gr["sku"].tolist())
    pred_con_gr["refuerzo_vespertino"] = pred_con_gr["sku"].map(
        lambda s: bool(refuerzos.get(s, False))
    )

    # -----------------------------------------------------------
    # 5. Persistencia en BD y archivos
    # -----------------------------------------------------------
    operation_mode = settings.OPERATION_MODE
    fecha_str = fecha_objetivo.isoformat()
    timestamp = datetime.now().isoformat()

    with get_connection() as conn:
        # Borrar predicciones previas del mismo (fecha, modelo) por idempotencia
        conn.execute(
            "DELETE FROM predictions WHERE prediction_date = ? AND model_version = ?",
            (fecha_str, model_version),
        )
        for _, r in pred_con_gr.iterrows():
            conn.execute(
                """
                INSERT INTO predictions (
                    prediction_date, sku, categoria, pred_raw, pred_final,
                    guardrails_applied, bottles, refuerzo_vespertino,
                    model_version, operation_mode, created_by
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fecha_str, r["sku"], r.get("categoria"),
                    float(r["pred_raw"]), float(r["pred_final"]),
                    json.dumps(r["guardrails_applied"]),
                    int(r["bottles"]),
                    1 if r["refuerzo_vespertino"] else 0,
                    model_version, operation_mode, user_id,
                ),
            )

    # Archivos de salida (PDF/CSV/log) - apartado generado por pdf_renderer
    yyyymmdd = fecha_objetivo.strftime("%Y%m%d")
    csv_path = settings.OUTPUTS_DIR / f"predicciones_{yyyymmdd}.csv"
    log_path = settings.OUTPUTS_DIR / f"logs_{yyyymmdd}.json"
    pdf_path = settings.OUTPUTS_DIR / f"orden_surtido_{yyyymmdd}.pdf"

    pred_con_gr.to_csv(csv_path, index=False)

    # Resumen para respuesta
    summary = resumir_guardrails(pred_con_gr)
    n_skus = len(pred_con_gr)
    total_bottles = int(pred_con_gr["bottles"].sum())
    total_units = float(pred_con_gr["pred_final"].sum())

    # Log estructurado JSON
    log_data = {
        "prediction_date": fecha_str,
        "generated_at": timestamp,
        "model_version": model_version,
        "operation_mode": operation_mode,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "n_skus": n_skus,
        "total_bottles": total_bottles,
        "total_units": total_units,
        "guardrails_summary": summary,
        "es_dia_especial": es_especial,
        "created_by_user_id": user_id,
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)

    # Lista de predicciones para la respuesta
    items = []
    for _, r in pred_con_gr.iterrows():
        items.append({
            "sku": r["sku"],
            "categoria": r.get("categoria"),
            "pred_raw": float(r["pred_raw"]),
            "pred_final": float(r["pred_final"]),
            "guardrails_applied": r["guardrails_applied"],
            "bottles": int(r["bottles"]),
            "refuerzo_vespertino": bool(r["refuerzo_vespertino"]),
        })

    return {
        "prediction_date": fecha_objetivo,
        "model_version": model_version,
        "operation_mode": operation_mode,
        "n_skus_predicted": n_skus,
        "total_bottles": total_bottles,
        "total_units": total_units,
        "guardrails_summary": summary,
        "predictions": items,
        "pdf_path": str(pdf_path),
        "csv_path": str(csv_path),
        "log_path": str(log_path),
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
    }