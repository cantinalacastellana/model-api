# ============================================================
# routers/metrics_router.py - METRICAS PARA TIER B
# ------------------------------------------------------------
# Estos endpoints producen exactamente los datos que entran en el
# paquete 04_TIER_B_METRICAS/ del entregable M4:
#
#   GET /metrics/adoption   -> metrica de adopcion (Tier B M4 Sec 5.2)
#   GET /metrics/impact     -> impact proxy (notas a barra)
#   GET /metrics/dashboard  -> agregado para el frontend
#   GET /metrics/wape       -> WAPE observado por dia (evidencia tecnica)
#
# La ventana de medicion es parametrizable (default = ultimos 30 dias)
# para alinearse con la "ventana temporal" pedida por Tier B.
# ============================================================

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
import pandas as pd

from auth import get_current_user
from database import get_connection
from services.drift_service import calcular_wape_ponderado

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get(
    "/adoption",
    summary="Metrica de adopcion (Tier B: tasa de firma + dias con uso)",
)
async def adoption(
    user: Annotated[dict, Depends(get_current_user)],
    desde: date | None = Query(None, description="Fecha de inicio (default: hace 30 dias)"),
    hasta: date | None = Query(None, description="Fecha de fin (default: hoy)"),
):
    """
    Calcula la tasa de adopcion del sistema en una ventana:

      - dias_con_prediccion: dias en que se genero la prediccion
      - dias_firmados: dias con signoff
      - tasa_firma: dias_firmados / dias_con_prediccion
      - dias_firmados_sin_modificacion: signoffs con n_modifications=0
      - tasa_firma_sin_mod: KEY METRIC del M3 (>=80% objetivo)

    Tier B M4 (Seccion 5.2) define adopcion como "uso repetido en una
    ventana de tiempo definida". Esto es exactamente lo que mide aqui.
    """
    if hasta is None:
        hasta = date.today()
    if desde is None:
        desde = hasta - timedelta(days=30)

    desde_s = desde.isoformat()
    hasta_s = hasta.isoformat()
    ventana_dias = (hasta - desde).days + 1

    with get_connection() as conn:
        dias_pred = conn.execute(
            """SELECT COUNT(DISTINCT prediction_date) AS d
               FROM predictions
               WHERE prediction_date BETWEEN ? AND ?""",
            (desde_s, hasta_s),
        ).fetchone()["d"] or 0

        firmados = conn.execute(
            """SELECT COUNT(*) AS d
               FROM signoffs
               WHERE prediction_date BETWEEN ? AND ?""",
            (desde_s, hasta_s),
        ).fetchone()["d"] or 0

        firmados_sin_mod = conn.execute(
            """SELECT COUNT(*) AS d
               FROM signoffs
               WHERE prediction_date BETWEEN ? AND ?
                 AND n_modifications = 0""",
            (desde_s, hasta_s),
        ).fetchone()["d"] or 0

    tasa_firma = firmados / dias_pred if dias_pred > 0 else 0.0
    tasa_sin_mod = firmados_sin_mod / firmados if firmados > 0 else 0.0
    cobertura = dias_pred / ventana_dias if ventana_dias > 0 else 0.0

    return {
        "ventana": {"desde": desde, "hasta": hasta, "n_dias": ventana_dias},
        "dias_con_prediccion": dias_pred,
        "dias_firmados": firmados,
        "dias_firmados_sin_modificacion": firmados_sin_mod,
        "tasa_firma": round(tasa_firma, 4),
        "tasa_firma_sin_modificacion": round(tasa_sin_mod, 4),
        "cobertura_temporal": round(cobertura, 4),
        "umbral_objetivo_freeze": 0.80,
        "cumple_umbral_freeze": tasa_sin_mod >= 0.80,
    }


@router.get(
    "/impact",
    summary="Impact proxy: notas a barra por dia (Tier B M4)",
)
async def impact(
    user: Annotated[dict, Depends(get_current_user)],
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
):
    """
    El KPI principal de impacto: numero de notas a barra por dia.
    Cada nota = una reposicion intra-dia = una falla del surtido inicial.
    Objetivo del proyecto: REDUCIR este numero (de un baseline de ~3-5
    notas/dia documentado en M3).
    """
    if hasta is None:
        hasta = date.today()
    if desde is None:
        desde = hasta - timedelta(days=30)

    desde_s = desde.isoformat()
    hasta_s = hasta.isoformat()

    with get_connection() as conn:
        df = pd.read_sql_query(
            """
            SELECT nota_date, COUNT(*) AS n_notas,
                   SUM(quantity) AS units_repuestas,
                   COUNT(DISTINCT sku) AS n_skus_afectados
            FROM notas_barra
            WHERE nota_date BETWEEN ? AND ?
            GROUP BY nota_date
            ORDER BY nota_date
            """,
            conn, params=[desde_s, hasta_s],
        )

    if len(df) == 0:
        return {
            "ventana": {"desde": desde, "hasta": hasta},
            "n_dias_con_notas": 0,
            "notas_promedio_por_dia": 0,
            "units_repuestas_total": 0,
            "detalle_por_dia": [],
        }

    return {
        "ventana": {"desde": desde, "hasta": hasta},
        "n_dias_con_notas": len(df),
        "notas_total": int(df["n_notas"].sum()),
        "notas_promedio_por_dia": round(float(df["n_notas"].mean()), 2),
        "units_repuestas_total": int(df["units_repuestas"].sum()),
        "detalle_por_dia": df.to_dict("records"),
    }


@router.get(
    "/wape",
    summary="WAPE observado por dia (evidencia tecnica para reporte final)",
)
async def wape_observado(
    user: Annotated[dict, Depends(get_current_user)],
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
):
    """
    WAPE Ponderado Logistico calculado por dia comparando predicciones
    vs ventas reales. Esta es la metrica ancla del proyecto.
    """
    if hasta is None:
        hasta = date.today()
    if desde is None:
        desde = hasta - timedelta(days=30)

    with get_connection() as conn:
        df = pd.read_sql_query(
            """
            SELECT p.prediction_date AS Fecha, p.sku, p.pred_final,
                   a.units_sold AS Venta_Real,
                   COALESCE(a.factor_impacto, 1.0) AS Factor_Impacto_Total
            FROM predictions p
            INNER JOIN actual_sales a
              ON a.sale_date = p.prediction_date AND a.sku = p.sku
            WHERE p.prediction_date BETWEEN ? AND ?
            """,
            conn, params=[desde.isoformat(), hasta.isoformat()],
        )

    if len(df) == 0:
        return {"ventana": {"desde": desde, "hasta": hasta}, "wapes": []}

    resultados = []
    for fecha_g, grupo in df.groupby("Fecha"):
        resultados.append({
            "fecha": str(fecha_g),
            "n_skus": len(grupo),
            "wape_ponderado": round(calcular_wape_ponderado(grupo), 2),
        })

    wape_global = round(calcular_wape_ponderado(df), 2)
    return {
        "ventana": {"desde": desde, "hasta": hasta},
        "wape_ponderado_global_ventana": wape_global,
        "wape_objetivo_freeze_M3": 109.66,  # del Freeze del M3
        "n_dias_con_evaluacion": len(resultados),
        "wapes_por_dia": resultados,
    }


@router.get(
    "/dashboard",
    summary="Resumen agregado para el dashboard del frontend",
)
async def dashboard(
    user: Annotated[dict, Depends(get_current_user)],
):
    """
    Endpoint conveniencia para el frontend: agrega los KPIs principales
    de los ultimos 30 dias en una sola respuesta.
    """
    hasta = date.today()
    desde = hasta - timedelta(days=30)

    # Llamar a los endpoints en BD directamente
    adop = await adoption(user=user, desde=desde, hasta=hasta)
    imp = await impact(user=user, desde=desde, hasta=hasta)
    wape = await wape_observado(user=user, desde=desde, hasta=hasta)

    with get_connection() as conn:
        last_drift = conn.execute(
            "SELECT * FROM drift_checks ORDER BY check_date DESC LIMIT 1"
        ).fetchone()
        active_model = conn.execute(
            "SELECT version FROM model_versions WHERE is_active = 1 LIMIT 1"
        ).fetchone()

    return {
        "ventana_dias": 30,
        "active_model_version": active_model["version"] if active_model else None,
        "adoption": adop,
        "impact": imp,
        "wape": wape,
        "last_drift_check": dict(last_drift) if last_drift else None,
    }
