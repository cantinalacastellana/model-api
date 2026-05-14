# ============================================================
# routers/logs_router.py
# ------------------------------------------------------------
# Endpoints de registro operativo - SON LA EVIDENCIA TIER B:
#
#   POST /logs/nota-barra                  -> registra una reposicion intra-dia
#   GET  /logs/notas/{fecha}               -> lista notas del dia
#   POST /logs/actual-sale                 -> registra venta real (cierre)
#   GET  /logs/sales/{fecha}               -> lista ventas del dia
#   POST /logs/actual-sales/bulk           -> carga masiva de ventas via CSV
#   GET  /logs/daily-comparison/{fecha}    -> prediccion vs real vs notas
#
# La metrica "numero de notas a barra por dia" es el IMPACT PROXY
# directo del M4 (Seccion 5.2 Tier B): el objetivo del sistema es
# REDUCIRLA. Cada nota que se registra es evidencia auditable que
# alimenta el paquete 04_TIER_B_METRICAS/.
# ============================================================

from datetime import date
from io import StringIO
from typing import Annotated
import base64
import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth import get_current_user, require_roles
from database import get_connection, log_audit, rows_to_list
from schemas.predict import (
    NotaBarraRequest, ActualSaleRequest, DailyComparisonResponse,
)
from services.drift_service import calcular_wape_ponderado, calcular_wape_simple

router = APIRouter(prefix="/logs", tags=["logs"])


# ============================================================
# Notas a barra (impact proxy Tier B)
# ============================================================
@router.post(
    "/nota-barra",
    status_code=status.HTTP_201_CREATED,
    summary="Registrar una nota a barra (reposicion intra-dia)",
)
async def registrar_nota_barra(
    payload: NotaBarraRequest,
    request: Request,
    user: Annotated[dict, Depends(require_roles("barman", "manager", "admin"))],
):
    """
    El barman registra una reposicion intra-dia. Cada nota es una falla
    operativa: representa una unidad que el corredor tuvo que bajar de
    bodega porque el surtido inicial no la habia incluido. La metrica
    'notas por dia' es el IMPACT PROXY para Tier B (Seccion 5.2 M4).
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO notas_barra (
                nota_date, sku, quantity, bloque_horario, reason, reported_by
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.nota_date.isoformat(),
                payload.sku, payload.quantity,
                payload.bloque_horario, payload.reason,
                user["id"],
            ),
        )
        nota_id = cur.lastrowid

    log_audit(
        user["id"], "nota_barra_registrada",
        endpoint="/logs/nota-barra",
        ip_address=request.client.host if request.client else None,
        details={
            "nota_id": nota_id, "sku": payload.sku,
            "quantity": payload.quantity, "date": str(payload.nota_date),
        },
    )
    return {
        "nota_id": nota_id,
        "nota_date": payload.nota_date,
        "sku": payload.sku,
        "quantity": payload.quantity,
        "registered_by": user["username"],
    }


@router.get("/notas/{fecha}", summary="Listar notas a barra del dia")
async def listar_notas(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT n.*, u.username AS reported_by_username
            FROM notas_barra n
            LEFT JOIN users u ON u.id = n.reported_by
            WHERE n.nota_date = ?
            ORDER BY n.reported_at
            """,
            (fecha.isoformat(),),
        ).fetchall()
    return {
        "nota_date": fecha,
        "n_notas": len(rows),
        "total_units": sum(r["quantity"] for r in rows),
        "notas": rows_to_list(rows),
    }


# ============================================================
# Ventas reales (insumo para comparacion shadow y retrain staging)
# ============================================================
@router.post(
    "/actual-sale",
    status_code=status.HTTP_201_CREATED,
    summary="Registrar venta real de un SKU al cierre del dia",
)
async def registrar_venta_real(
    payload: ActualSaleRequest,
    request: Request,
    user: Annotated[dict, Depends(require_roles("manager", "admin"))],
):
    """
    Al cierre de jornada, el gerente registra las unidades realmente
    vendidas por SKU. Estos datos alimentan:
      1. La comparacion shadow (predicted vs actual)
      2. El staging/ para reentrenamiento futuro
      3. El WAPE observado para chequear model drift
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO actual_sales (
                sale_date, sku, units_sold, factor_impacto, reported_by
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.sale_date.isoformat(), payload.sku,
                payload.units_sold, payload.factor_impacto, user["id"],
            ),
        )

    log_audit(
        user["id"], "venta_real_registrada",
        endpoint="/logs/actual-sale",
        details={
            "date": str(payload.sale_date),
            "sku": payload.sku, "units_sold": payload.units_sold,
        },
    )
    return {"status": "ok"}


@router.post(
    "/actual-sales/bulk",
    summary="Carga masiva de ventas reales via CSV base64",
)
async def registrar_ventas_bulk(
    payload: dict,
    request: Request,
    user: Annotated[dict, Depends(require_roles("manager", "admin"))],
):
    """
    Acepta un JSON con {"csv_b64": "..."} donde el CSV tiene columnas
    [sale_date, sku, units_sold, factor_impacto?]. Util para subir
    el cierre del POS de un solo golpe.
    """
    csv_b64 = payload.get("csv_b64")
    if not csv_b64:
        raise HTTPException(400, "Falta csv_b64")
    try:
        raw = base64.b64decode(csv_b64).decode("utf-8")
        df = pd.read_csv(StringIO(raw))
    except Exception as e:
        raise HTTPException(400, f"CSV invalido: {e}")

    requeridas = {"sale_date", "sku", "units_sold"}
    if not requeridas.issubset(df.columns):
        raise HTTPException(400, f"Faltan columnas: {requeridas - set(df.columns)}")

    insertadas = 0
    with get_connection() as conn:
        for _, r in df.iterrows():
            conn.execute(
                """
                INSERT OR REPLACE INTO actual_sales (
                    sale_date, sku, units_sold, factor_impacto, reported_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(r["sale_date"])[:10], str(r["sku"]),
                    int(r["units_sold"]),
                    float(r["factor_impacto"]) if "factor_impacto" in df.columns and pd.notna(r.get("factor_impacto")) else None,
                    user["id"],
                ),
            )
            insertadas += 1

    log_audit(
        user["id"], "ventas_bulk_upload",
        endpoint="/logs/actual-sales/bulk",
        details={"rows_inserted": insertadas},
    )
    return {"status": "ok", "rows_inserted": insertadas}


@router.get("/sales/{fecha}", summary="Listar ventas reales del dia")
async def listar_ventas(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM actual_sales WHERE sale_date = ? ORDER BY sku",
            (fecha.isoformat(),),
        ).fetchall()
    return {
        "sale_date": fecha,
        "n_skus": len(rows),
        "total_units": sum(r["units_sold"] for r in rows),
        "sales": rows_to_list(rows),
    }


# ============================================================
# Comparacion diaria (shadow mode)
# ============================================================
@router.get(
    "/daily-comparison/{fecha}",
    response_model=DailyComparisonResponse,
    summary="Comparacion del dia: prediccion vs venta real vs notas",
)
async def comparacion_diaria(
    fecha: date,
    user: Annotated[dict, Depends(get_current_user)],
):
    """
    Genera la comparacion completa de un dia operado:
      - predicted: lo que el modelo dijo
      - actual: lo que se vendio (registrado por gerente)
      - notas: las reposiciones intra-dia (impact proxy Tier B)
      - WAPE observado vs el WAPE de validacion del modelo
    """
    fecha_str = fecha.isoformat()
    with get_connection() as conn:
        df_pred = pd.read_sql_query(
            "SELECT sku, pred_final FROM predictions WHERE prediction_date = ?",
            conn, params=[fecha_str],
        )
        df_real = pd.read_sql_query(
            """SELECT sku, units_sold AS Venta_Real,
                      COALESCE(factor_impacto, 1.0) AS Factor_Impacto_Total
               FROM actual_sales WHERE sale_date = ?""",
            conn, params=[fecha_str],
        )
        df_notas = pd.read_sql_query(
            """SELECT sku, SUM(quantity) AS notas_units
               FROM notas_barra WHERE nota_date = ? GROUP BY sku""",
            conn, params=[fecha_str],
        )

    n_pred = len(df_pred)
    n_real = len(df_real)
    total_pred = float(df_pred["pred_final"].sum()) if n_pred > 0 else 0
    total_real = float(df_real["Venta_Real"].sum()) if n_real > 0 else 0

    # WAPE observado - merge predicciones con ventas reales
    wape_p = None
    wape_s = None
    if n_pred > 0 and n_real > 0:
        merged = df_pred.merge(df_real, on="sku", how="inner")
        if len(merged) > 0:
            merged = merged.rename(columns={"pred_final": "pred_final"})
            wape_p = calcular_wape_ponderado(merged, "pred_final", "Venta_Real")
            wape_s = calcular_wape_simple(merged, "pred_final", "Venta_Real")

    # Notas vs baseline - este es el numero clave para Tier B
    notas_vs_baseline = None
    if len(df_notas) > 0:
        notas_total = int(df_notas["notas_units"].sum())
        notas_vs_baseline = {
            "notas_units_today": notas_total,
            "n_skus_con_nota": len(df_notas),
        }

    return DailyComparisonResponse(
        comparison_date=fecha,
        n_skus_predicted=n_pred,
        n_skus_with_sales=n_real,
        n_notas_barra=int(df_notas["notas_units"].sum()) if len(df_notas) > 0 else 0,
        total_units_predicted=total_pred,
        total_units_sold=total_real,
        wape_simple_observed=wape_s,
        wape_ponderado_observed=wape_p,
        notas_vs_baseline=notas_vs_baseline,
    )
