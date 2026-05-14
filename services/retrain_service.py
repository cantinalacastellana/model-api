# ============================================================
# services/retrain_service.py
# ------------------------------------------------------------
# Reentrenamiento MANUAL (invocado desde /admin/retrain). NUNCA
# automatico. Cumple con la Seccion 3.6 del M4 (reproducibilidad
# y trazabilidad cuando hay tuning/search): cada configuracion
# probada queda registrada en BD para auditoria.
#
# Flujo:
#   1. Cargar dataset (snapshot original + opcionalmente staging)
#   2. Construir features con anti-leakage
#   3. Split train / val / test (mismos cortes del Freeze)
#   4. Para CADA config del GRID_CONFIGS:
#        - Entrenar LightGBM con esa config
#        - Calcular WAPE_Ponderado en VAL
#        - Registrar la corrida
#   5. Elegir best_config (menor WAPE_Pond_VAL)
#   6. Comparar contra modelo activo:
#        - Si best supera al actual en VAL -> promote
#        - Si no -> reject (mantener modelo actual)
#   7. Registrar todo en retrain_jobs
#
# Si force_promote=True, promueve aun si pierde (uso restringido,
# solo cuando el modelo actual esta roto).
# ============================================================

from __future__ import annotations
from datetime import datetime
import json
import time
import joblib
import pandas as pd
import numpy as np
from pathlib import Path

import lightgbm as lgb

from config import get_settings
from database import get_connection
from services.feature_engineering import construir_features, FEATURE_COLUMNS
from services.drift_service import calcular_wape_ponderado, calcular_wape_simple


# Fechas oficiales del Freeze M3 (NO cambiar)
FECHA_CORTE_TRAIN = pd.Timestamp("2025-12-19")
FECHA_CORTE_VAL = pd.Timestamp("2026-01-19")


def _cargar_dataset(include_staging: bool) -> pd.DataFrame:
    """
    Carga el snapshot original del Freeze. Si include_staging=True,
    fusiona con los logs de actual_sales acumulados.

    NOTA: el snapshot original NUNCA se modifica. Si se fusiona staging,
    se construye un dataset combinado temporal solo para el entrenamiento.
    """
    settings = get_settings()
    snapshot_path = settings.MODEL_ORIGINAL_DIR / settings.SNAPSHOT_ORIGINAL_FILENAME
    if not snapshot_path.exists():
        raise FileNotFoundError(
            f"No se encuentra el snapshot original en {snapshot_path}. "
            "Coloca df_ml_ready_for_M3.csv en data/models/original/"
        )

    df_original = pd.read_csv(snapshot_path)
    df_original["Fecha"] = pd.to_datetime(df_original["Fecha"])

    # Si el snapshot trae Bloque_Horario, agregarlo a diario
    if "Bloque_Horario" in df_original.columns:
        df_original = (
            df_original.groupby(
                ["Fecha", "SKU_Operativo", "Categoria"], as_index=False
            )
            .agg({
                "Venta_Real": "sum",
                "Factor_Impacto_Total": "first",
            })
        )
        # NombreDia
        df_original["NombreDia"] = df_original["Fecha"].dt.day_name()

    if not include_staging:
        return df_original

    # Cargar staging (ventas reales registradas via API)
    with get_connection() as conn:
        df_staging = pd.read_sql_query(
            """
            SELECT sale_date AS Fecha, sku AS SKU_Operativo,
                   units_sold AS Venta_Real,
                   COALESCE(factor_impacto, 1.0) AS Factor_Impacto_Total
            FROM actual_sales
            """,
            conn,
        )
    if len(df_staging) == 0:
        return df_original

    df_staging["Fecha"] = pd.to_datetime(df_staging["Fecha"])
    # Solo agregar fechas posteriores al snapshot original
    fecha_max_original = df_original["Fecha"].max()
    df_staging = df_staging[df_staging["Fecha"] > fecha_max_original]
    if len(df_staging) == 0:
        return df_original

    # Heredar Categoria del original (lookup por SKU)
    cat_map = df_original.groupby("SKU_Operativo")["Categoria"].first().to_dict()
    df_staging["Categoria"] = df_staging["SKU_Operativo"].map(cat_map).fillna("Varios")

    df_combinado = pd.concat([df_original, df_staging], ignore_index=True)
    return df_combinado


def _entrenar_una_config(
    df_train: pd.DataFrame,
    df_val: pd.DataFrame,
    config: tuple,
) -> dict:
    """
    Entrena LightGBM con una configuracion. Retorna dict con resultados.
    Anti-leakage: usa solo df_train para fit, df_val para early stopping.
    """
    name, num_leaves, lr, mcs, l2 = config
    settings = get_settings()
    t0 = time.time()

    X_train = df_train[FEATURE_COLUMNS].fillna(0).values
    y_train = df_train["Venta_Real_Clipped"].values
    w_train = df_train["Factor_Impacto_Total"].values

    X_val = df_val[FEATURE_COLUMNS].fillna(0).values
    y_val = df_val["Venta_Real"].values
    w_val = df_val["Factor_Impacto_Total"].values

    model = lgb.LGBMRegressor(
        objective="regression_l1",  # MAE
        n_estimators=2000,  # Tope; early stopping decide
        num_leaves=num_leaves,
        learning_rate=lr,
        min_child_samples=mcs,
        reg_lambda=l2,
        random_state=settings.SEED,
        n_jobs=-1,
        verbose=-1,
    )

    model.fit(
        X_train, y_train,
        sample_weight=w_train,
        eval_set=[(X_val, y_val)],
        eval_sample_weight=[w_val],
        callbacks=[
            lgb.early_stopping(settings.EARLY_STOPPING_ROUNDS, verbose=False),
        ],
    )

    iterations = model.best_iteration_ or model.n_estimators
    # WAPE en validacion
    preds_val = np.clip(np.round(model.predict(X_val)), 0, None).astype(int)
    df_val_eval = df_val.copy()
    df_val_eval["pred_final"] = preds_val
    wape_p = calcular_wape_ponderado(df_val_eval, "pred_final", "Venta_Real")
    wape_s = calcular_wape_simple(df_val_eval, "pred_final", "Venta_Real")

    duration = time.time() - t0
    return {
        "config_name": name,
        "num_leaves": num_leaves,
        "learning_rate": lr,
        "min_child_samples": mcs,
        "lambda_l2": l2,
        "wape_ponderado_val": wape_p,
        "wape_simple_val": wape_s,
        "iterations_trained": iterations,
        "duration_seconds": duration,
        "_model_obj": model,  # interno; se serializa solo el ganador
    }


def ejecutar_reentrenamiento(
    user_id: int,
    include_staging: bool = True,
    notes: str = "",
    force_promote: bool = False,
) -> dict:
    """
    Punto de entrada del retrain. Bloqueante: la ejecucion tarda varios
    minutos. Si el frontend necesita asincronia, llamarlo en background.
    """
    settings = get_settings()

    # Crear registro de job
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO retrain_jobs (started_by, notes) VALUES (?, ?)",
            (user_id, notes),
        )
        job_id = cur.lastrowid

    try:
        # 1. Cargar dataset
        df_raw = _cargar_dataset(include_staging)

        # 2. Construir features
        df_feat = construir_features(df_raw, fecha_corte_train=FECHA_CORTE_TRAIN)

        # 3. Split temporal
        df_train = df_feat[df_feat["Fecha"] <= FECHA_CORTE_TRAIN].copy()
        df_val = df_feat[
            (df_feat["Fecha"] > FECHA_CORTE_TRAIN)
            & (df_feat["Fecha"] <= FECHA_CORTE_VAL)
        ].copy()

        # Eliminar filas con NaN en columnas critic (lag_7 al inicio)
        df_train = df_train.dropna(subset=["Lag_7"])
        df_val = df_val.dropna(subset=["Lag_7"])

        # 4. Grid search
        resultados = []
        for config in settings.GRID_CONFIGS:
            try:
                r = _entrenar_una_config(df_train, df_val, config)
                resultados.append(r)
            except Exception as e:
                resultados.append({
                    "config_name": config[0],
                    "error": str(e)[:300],
                    "wape_ponderado_val": float("inf"),
                })

        # 5. Elegir mejor
        validos = [r for r in resultados if "error" not in r]
        if not validos:
            raise RuntimeError("Todas las configuraciones fallaron al entrenar")

        best = min(validos, key=lambda r: r["wape_ponderado_val"])

        # 6. Comparar con modelo activo
        with get_connection() as conn:
            row_actual = conn.execute(
                "SELECT version, wape_val FROM model_versions WHERE is_active = 1 LIMIT 1"
            ).fetchone()
        current_wape = row_actual["wape_val"] if row_actual else float("inf")

        if best["wape_ponderado_val"] < current_wape or force_promote:
            decision = "promote"
            # Serializar y registrar
            new_version = f"lgbm_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{best['config_name']}"
            new_path = settings.MODEL_CANDIDATES_DIR / f"{new_version}.joblib"
            joblib.dump(best["_model_obj"], new_path)

            with get_connection() as conn:
                # Desactivar el modelo actual
                conn.execute("UPDATE model_versions SET is_active = 0")
                # Insertar y activar el nuevo
                conn.execute(
                    """
                    INSERT INTO model_versions (
                        version, path, is_active, is_original,
                        wape_val, config_name, config_params,
                        promoted_at, notes
                    ) VALUES (?,?,1,0,?,?,?, datetime('now'), ?)
                    """,
                    (
                        new_version, str(new_path), best["wape_ponderado_val"],
                        best["config_name"],
                        json.dumps({
                            "num_leaves": best["num_leaves"],
                            "learning_rate": best["learning_rate"],
                            "min_child_samples": best["min_child_samples"],
                            "lambda_l2": best["lambda_l2"],
                            "iterations": best["iterations_trained"],
                        }),
                        f"Promovido tras grid search. WAPE prev={current_wape:.2f}%. {notes}",
                    ),
                )
        else:
            decision = "reject"
            new_version = None
            # Modelo actual se queda activo. FAILSAFE OK.

        # 7. Cerrar el job
        finished = datetime.now().isoformat()
        configs_test_serial = json.dumps([
            {k: v for k, v in r.items() if k != "_model_obj"}
            for r in resultados
        ], default=str)

        with get_connection() as conn:
            conn.execute(
                """
                UPDATE retrain_jobs
                SET finished_at = ?, status = 'completed', configs_tested = ?,
                    best_config_name = ?, best_wape_val = ?, current_wape_val = ?,
                    decision = ?, new_model_version = ?
                WHERE id = ?
                """,
                (
                    finished, configs_test_serial, best["config_name"],
                    best["wape_ponderado_val"], current_wape, decision,
                    new_version, job_id,
                ),
            )

        return {
            "job_id": job_id,
            "status": "completed",
            "configs_tested": [
                {k: v for k, v in r.items() if k != "_model_obj"}
                for r in resultados
            ],
            "best_config_name": best["config_name"],
            "best_wape_val": best["wape_ponderado_val"],
            "current_wape_val": current_wape,
            "decision": decision,
            "new_model_version": new_version,
        }

    except Exception as e:
        # Fallar de forma controlada
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE retrain_jobs
                SET finished_at = datetime('now'), status = 'failed',
                    error_message = ?
                WHERE id = ?
                """,
                (str(e)[:1000], job_id),
            )
        raise
