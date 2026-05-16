# ============================================================
# routers/danger_router.py
# ------------------------------------------------------------
# Pestana DANGER ZONE - acciones destructivas, solo admin REAL
# (no admin_demo). Util para preparar la BD antes de demos al
# sinodal y para purgarla antes de entrar en operativa real.
#
# Endpoints:
#   POST /danger/jumpstart  -> popular BD con un mes de actividad
#                              realista (predictions, signoffs,
#                              ventas, notas, drift, audit_log)
#   POST /danger/purge      -> borrar TODA la operativa, dejando
#                              solo users + model_versions
#   GET  /danger/status     -> contador de filas por tabla
#
# Guard:
#   - Requiere rol admin
#   - Bloquea username="admin_demo" (es solo para demo cuentas)
#   - Cualquier admin con username distinto puede invocar
# ============================================================

from __future__ import annotations
import json
import random
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth import get_current_user
from database import get_connection, log_audit

router = APIRouter(prefix="/danger", tags=["danger"])


# ============================================================
# Guard: solo admin real (no demo)
# ============================================================
def require_admin_real(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    if user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Danger Zone requiere rol admin.",
        )
    if user["username"] == "admin_demo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Danger Zone esta deshabilitado para la cuenta admin_demo. "
                "Usa el admin real del sistema."
            ),
        )
    return user


# ============================================================
# Status
# ============================================================
TABLAS_OPERATIVAS = [
    "predictions", "signoffs", "notas_barra", "actual_sales",
    "drift_checks", "llm_alerts", "audit_log", "retrain_jobs",
]


@router.get("/status", summary="Contar filas por tabla operativa")
async def status_bd(admin: Annotated[dict, Depends(require_admin_real)]):
    counts: dict[str, int] = {}
    with get_connection() as conn:
        for t in TABLAS_OPERATIVAS:
            n = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            counts[t] = n
        users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        models = conn.execute("SELECT COUNT(*) AS c FROM model_versions").fetchone()["c"]
    return {
        "operativas": counts,
        "users": users,
        "model_versions": models,
        "total_operativo": sum(counts.values()),
    }


# ============================================================
# JUMPSTART
# ============================================================
@router.post("/jumpstart", summary="Popular BD con un mes de actividad demo")
async def jumpstart(
    request: Request,
    admin: Annotated[dict, Depends(require_admin_real)],
    dias: int = 30,
    overwrite: bool = False,
):
    """
    Inserta `dias` dias de actividad operativa hacia atras desde hoy,
    SIMULANDO uso del sistema. Util para preparar pantallas para una
    demo al sinodal sin esperar a que se acumule historia real.

    No requiere SKUs reales (se construyen sinteticos de las categorias
    tipicas). Si overwrite=False y ya hay >50 filas operativas, falla.
    """
    if dias < 1 or dias > 365:
        raise HTTPException(400, "dias debe estar entre 1 y 365")

    # ---------- chequeo de overwrite ----------
    with get_connection() as conn:
        n_existentes = conn.execute(
            "SELECT COUNT(*) AS c FROM predictions"
        ).fetchone()["c"]
    if n_existentes > 50 and not overwrite:
        raise HTTPException(
            409,
            f"Ya hay {n_existentes} predicciones. Pasa overwrite=true para "
            "limpiar primero o invoca /danger/purge.",
        )

    # ---------- usuarios necesarios ----------
    with get_connection() as conn:
        gerente = conn.execute(
            "SELECT id FROM users WHERE role='manager' LIMIT 1"
        ).fetchone()
        barman = conn.execute(
            "SELECT id FROM users WHERE role='barman' LIMIT 1"
        ).fetchone()
        modelo_activo = conn.execute(
            "SELECT version FROM model_versions WHERE is_active = 1 LIMIT 1"
        ).fetchone()

    if not gerente:
        raise HTTPException(500, "No hay usuario con rol 'manager'. Crea uno primero.")
    if not barman:
        raise HTTPException(500, "No hay usuario con rol 'barman'. Crea uno primero.")
    if not modelo_activo:
        raise HTTPException(500, "No hay modelo activo. Sube un modelo primero.")

    gerente_id = gerente["id"]
    barman_id = barman["id"]
    model_version = modelo_activo["version"]
    admin_id = admin["id"]

    rng = random.Random(232106)  # misma seed del Freeze M3 para reproducibilidad

    # ---------- limpiar antes si overwrite ----------
    if overwrite:
        with get_connection() as conn:
            for t in TABLAS_OPERATIVAS:
                conn.execute(f"DELETE FROM {t}")

    # ---------- catalogo sintetico de SKUs ----------
    SKUS = _catalogo_skus()

    hoy = date.today()
    fechas = [hoy - timedelta(days=i) for i in range(dias, 0, -1)]

    n_preds = 0
    n_signoffs = 0
    n_ventas = 0
    n_notas = 0
    n_drift = 0
    n_audit = 0

    with get_connection() as conn:
        # Inicio del seed
        conn.execute(
            "INSERT INTO audit_log (user_id, action, endpoint, details, timestamp) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (admin_id, "danger_jumpstart_start", "/danger/jumpstart",
             json.dumps({"dias": dias, "overwrite": overwrite, "fecha_inicio": str(fechas[0]), "fecha_fin": str(fechas[-1])}, ensure_ascii=False)),
        )
        n_audit += 1

        for fecha in fechas:
            fecha_str = fecha.isoformat()
            es_fdsem = fecha.weekday() >= 4
            es_quincena = fecha.day in (14, 15, 30, 31)
            factor_dia = 1.3 if es_fdsem else 1.0
            factor_dia *= 1.15 if es_quincena else 1.0

            # Hora aproximada de uso operativo: el gerente firma ~9:30 AM
            ts_pred = f"{fecha_str} 08:{rng.randint(15, 55):02d}:{rng.randint(0,59):02d}"
            ts_sign = f"{fecha_str} 09:{rng.randint(20, 50):02d}:{rng.randint(0,59):02d}"

            # ----- PREDICTIONS -----
            modifs: dict[str, dict] = {}
            for sku, categoria, base_demand in SKUS:
                ruido = rng.uniform(0.75, 1.30)
                pred_raw = max(0.0, base_demand * factor_dia * ruido)
                # Guardrails: 10% de las veces se aplica GR1 o GR4
                gr = []
                pred_final = pred_raw
                if es_fdsem and rng.random() < 0.10:
                    gr.append("GR1")
                    pred_final = pred_raw * 1.30
                if rng.random() < 0.03:
                    gr.append("GR4")
                    pred_final = min(pred_final, 2.0)

                # Conversion simplificada a botellas
                bottles = 0
                if "(Sencillo)" in sku:
                    bottles = max(0, int((pred_final * 1.5) / 25.36) + (1 if (pred_final * 1.5) % 25.36 > 0 else 0))
                elif "(Doble)" in sku:
                    bottles = max(0, int((pred_final * 3.0) / 25.36) + (1 if (pred_final * 3.0) % 25.36 > 0 else 0))
                elif "(Botella)" in sku:
                    bottles = int(pred_final)

                conn.execute(
                    """INSERT OR REPLACE INTO predictions (
                        prediction_date, sku, categoria, pred_raw, pred_final,
                        guardrails_applied, bottles, refuerzo_vespertino,
                        model_version, operation_mode, created_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fecha_str, sku, categoria, pred_raw, pred_final,
                     json.dumps(gr), bottles, int(es_fdsem),
                     model_version, "piloto", ts_pred, admin_id),
                )
                n_preds += 1

                # Algunas modificaciones del gerente (~15% de SKUs cuando firma)
                if rng.random() < 0.15:
                    nuevo = max(0, int(pred_final * rng.uniform(0.6, 1.3)))
                    modifs[sku] = {"original": int(pred_final), "modified": nuevo}

            # ----- SIGNOFF -----
            # 85% de los dias el gerente firma (los demas se quedan sin firma,
            # simulando dias en que no se uso por la razon que sea)
            if rng.random() < 0.85:
                conn.execute(
                    """INSERT OR REPLACE INTO signoffs (
                        prediction_date, signed_by, signed_at,
                        modifications, n_modifications, notes, operation_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (fecha_str, gerente_id, ts_sign,
                     json.dumps(modifs, ensure_ascii=False),
                     len(modifs),
                     None if not modifs else f"Ajuste de {len(modifs)} SKUs por criterio de barra",
                     "piloto"),
                )
                n_signoffs += 1

                # Audit log de la firma
                conn.execute(
                    "INSERT INTO audit_log (user_id, action, endpoint, details, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (gerente_id, "signoff_created", "/predict/signoff",
                     json.dumps({"date": fecha_str, "n_modifications": len(modifs)}, ensure_ascii=False),
                     ts_sign),
                )
                n_audit += 1

            # ----- VENTAS REALES (ese dia) -----
            for sku, categoria, base_demand in SKUS:
                ruido_real = rng.uniform(0.65, 1.40)
                units = max(0, int(base_demand * factor_dia * ruido_real))
                ts_venta = f"{fecha_str} 23:{rng.randint(40,59):02d}:{rng.randint(0,59):02d}"
                conn.execute(
                    """INSERT OR REPLACE INTO actual_sales (
                        sale_date, sku, units_sold, factor_impacto,
                        reported_by, reported_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (fecha_str, sku, units, factor_dia, gerente_id, ts_venta),
                )
                n_ventas += 1

            # ----- NOTAS A BARRA (intra-dia, KPI Tier B) -----
            # Numero de notas baja en piloto: media de 2-4 por dia (vs 8-12 sin sistema)
            n_notas_dia = rng.choices([0, 1, 2, 3, 4, 5, 6], weights=[15, 20, 25, 20, 10, 7, 3])[0]
            bloques = ["09-13", "13-17", "17-21", "21-cierre"]
            for _ in range(n_notas_dia):
                sku = rng.choice(SKUS)[0]
                qty = rng.choices([1, 2, 3], weights=[60, 30, 10])[0]
                bloque = rng.choice(bloques)
                ts_nota = f"{fecha_str} {rng.randint(10,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}"
                conn.execute(
                    """INSERT INTO notas_barra (
                        nota_date, sku, quantity, bloque_horario, reason,
                        reported_by, reported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (fecha_str, sku, qty, bloque,
                     rng.choice(["Demanda no prevista", "Cliente VIP", "Promocion del dia", None]),
                     barman_id, ts_nota),
                )
                n_notas += 1

            # ----- DRIFT CHECKS (semanal) -----
            if fecha.weekday() == 0:  # lunes
                ks_pvalue = rng.uniform(0.05, 0.95)
                model_ratio = rng.uniform(0.85, 1.18)
                pipeline_health = rng.uniform(0.95, 1.0)
                alert = ks_pvalue < 0.01 or model_ratio > 1.20 or pipeline_health < 0.95
                reasons = []
                if ks_pvalue < 0.01: reasons.append("data_drift_ks")
                if model_ratio > 1.20: reasons.append("model_drift_ratio")
                if pipeline_health < 0.95: reasons.append("pipeline_health_low")

                ts_drift = f"{fecha_str} 06:00:00"
                conn.execute(
                    """INSERT OR REPLACE INTO drift_checks (
                        check_date, data_drift_ks_stat, data_drift_pvalue,
                        model_drift_wape_obs, model_drift_wape_exp, model_drift_ratio,
                        pipeline_health, alert_triggered, alert_reasons, checked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fecha_str, rng.uniform(0.05, 0.18), ks_pvalue,
                     109.6 * model_ratio, 109.6, model_ratio,
                     pipeline_health, int(alert), json.dumps(reasons), ts_drift),
                )
                n_drift += 1

        # Audit logs de logins distribuidos en el periodo
        for fecha in fechas:
            if rng.random() < 0.85:
                fecha_str = fecha.isoformat()
                ts = f"{fecha_str} 09:{rng.randint(5, 25):02d}:{rng.randint(0,59):02d}"
                conn.execute(
                    "INSERT INTO audit_log (user_id, action, endpoint, ip_address, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (gerente_id, "login_success", "/auth/login", "192.168.1." + str(rng.randint(10, 50)), ts),
                )
                n_audit += 1

        # Cierre del seed
        conn.execute(
            "INSERT INTO audit_log (user_id, action, endpoint, details, timestamp) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (admin_id, "danger_jumpstart_complete", "/danger/jumpstart",
             json.dumps({
                 "n_predictions": n_preds, "n_signoffs": n_signoffs,
                 "n_ventas": n_ventas, "n_notas": n_notas,
                 "n_drift_checks": n_drift, "n_audit": n_audit,
             }, ensure_ascii=False)),
        )
        n_audit += 1

    return {
        "status": "ok",
        "dias_simulados": dias,
        "fecha_inicio": fechas[0].isoformat(),
        "fecha_fin": fechas[-1].isoformat(),
        "n_predictions": n_preds,
        "n_signoffs": n_signoffs,
        "n_ventas_reales": n_ventas,
        "n_notas_barra": n_notas,
        "n_drift_checks": n_drift,
        "n_audit_entries": n_audit,
    }


# ============================================================
# PURGE
# ============================================================
@router.post("/purge", summary="Purgar TODA la BD operativa")
async def purge(
    request: Request,
    admin: Annotated[dict, Depends(require_admin_real)],
    confirm: str = "",
):
    """
    Borra TODAS las filas de las tablas operativas. Conserva:
      - users
      - model_versions
    Requiere confirm="PURGE_LA_CASTELLANA" como guard adicional.
    """
    if confirm != "PURGE_LA_CASTELLANA":
        raise HTTPException(
            400,
            "Confirmacion invalida. Pasa confirm='PURGE_LA_CASTELLANA' para proceder.",
        )

    counts_antes = {}
    with get_connection() as conn:
        for t in TABLAS_OPERATIVAS:
            counts_antes[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            conn.execute(f"DELETE FROM {t}")
        # Resetear contadores autoincrement de SQLite
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('predictions','signoffs','notas_barra','actual_sales','drift_checks','llm_alerts','audit_log','retrain_jobs')")
        except Exception:
            pass  # sqlite_sequence puede no existir si nunca hubo autoincrement
        # Reinsertar el log del purge mismo
        conn.execute(
            "INSERT INTO audit_log (user_id, action, endpoint, details, timestamp) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (admin["id"], "danger_purge_executed", "/danger/purge",
             json.dumps({"borrado": counts_antes}, ensure_ascii=False)),
        )

    return {
        "status": "ok",
        "borrado": counts_antes,
        "total_filas_borradas": sum(counts_antes.values()),
        "preservado": ["users", "model_versions"],
    }


# ============================================================
# Catalogo sintetico
# ============================================================
def _catalogo_skus() -> list[tuple[str, str, float]]:
    """
    (sku, categoria, base_demand) - demanda promedio diaria.
    Reproduce el perfil tipico de una cantina mexicana tradicional.
    """
    return [
        # Rones
        ("BACARDI BCO (Sencillo)", "Rones", 18),
        ("BACARDI BCO (Botella)", "Rones", 0.3),
        ("APPLETON ESP (Sencillo)", "Rones", 4),
        ("HAVANA 7 (Sencillo)", "Rones", 3),
        ("ZACAPA 23 (Sencillo)", "Rones", 2),
        # Tequilas
        ("TRADICIONAL REP (Sencillo)", "Tequilas", 12),
        ("TRADICIONAL REP (Doble)", "Tequilas", 2),
        ("TRADICIONAL REP (Botella)", "Tequilas", 0.2),
        ("DON JULIO 70 (Sencillo)", "Tequilas", 5),
        ("DON JULIO REP (Sencillo)", "Tequilas", 4),
        ("CUERVO 1800 REP (Sencillo)", "Tequilas", 6),
        ("HERR REP (Sencillo)", "Tequilas", 4),
        # Whiskys
        ("JW ETIQ. ROJA (Sencillo)", "Whiskys", 12),
        ("JW ETIQ. NEGRA (Sencillo)", "Whiskys", 4),
        ("BUCHANAN'S 12 (Sencillo)", "Whiskys", 5),
        ("CHIVAS REGAL 12 (Sencillo)", "Whiskys", 3),
        ("OLD PARR (Sencillo)", "Whiskys", 3),
        # Brandys
        ("TORRES 10 (Sencillo)", "Brandys", 10),
        ("DON PEDRO (Sencillo)", "Brandys", 4),
        ("FUNDADOR (Sencillo)", "Brandys", 5),
        ("PRESIDENTE (Sencillo)", "Brandys", 3),
        # Vodkas
        ("ABSOLUT AZUL (Sencillo)", "Vodkas", 4),
        ("SMIRNOFF (Sencillo)", "Vodkas", 5),
        # Ginebras
        ("BEEFEATER (Sencillo)", "Ginebras", 3),
        ("BOMBAY SAPPHIRE (Sencillo)", "Ginebras", 2),
        # Licores
        ("BAILEYS (Sencillo)", "Licores", 3),
        # Cognac
        ("COURVOISER (Sencillo)", "Cognac", 2),
        # Anis
        ("ANIS CHINCHON (Sencillo)", "Anis", 2),
        # Cocteles (tragos sueltos)
        ("MARGARITA", "Cocteles", 18),
        ("MOJITO", "Cocteles", 8),
        ("PINA COLADA", "Cocteles", 6),
        ("PALOMA", "Cocteles", 10),
        ("CUBA LIBRE", "Cocteles", 12),
        ("SANGRIA", "Cocteles", 4),
        ("PIEDRA", "Cocteles", 3),
        ("VAMPIRO", "Cocteles", 4),
        # Varios
        ("CERVEZA", "Varios", 70),
        ("REFRESCO", "Varios", 50),
        ("VASO MICHELADO", "Varios", 25),
        ("NOCHE BUENA", "Varios", 5),
    ]
