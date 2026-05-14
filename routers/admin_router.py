# ============================================================
# routers/admin_router.py
# ------------------------------------------------------------
# Endpoints administrativos (rol admin):
#
#   POST /admin/retrain                  -> reentrene manual con grid search
#   GET  /admin/retrain/jobs             -> historial
#   GET  /admin/retrain/jobs/{id}        -> detalle de un job
#
#   POST /admin/reset                    -> CRITICO: restaura modelo M3 original
#                                            (acceso adicional para 'sinodal')
#   GET  /admin/models                   -> lista versiones
#   POST /admin/models/{version}/promote -> activar version especifica
# ============================================================

from datetime import datetime
from typing import Annotated, List
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from auth import get_current_user, require_roles
from database import get_connection, log_audit, rows_to_list
from schemas.admin import (
    RetrainRequest, RetrainResponse, RetrainConfigResult,
    ModelVersionResponse, ResetResponse, PromoteRequest,
)
from services.retrain_service import ejecutar_reentrenamiento

router = APIRouter(prefix="/admin", tags=["admin"])


# ============================================================
# RETRAIN (manual, con grid search y failsafe)
# ============================================================
@router.post(
    "/retrain",
    response_model=RetrainResponse,
    summary="Reentrenamiento manual con grid search (rol admin)",
)
async def retrain(
    payload: RetrainRequest,
    request: Request,
    admin: Annotated[dict, Depends(require_roles("admin"))],
):
    """
    Inicia un reentrenamiento manual. Prueba todas las configuraciones
    del grid (GRID_CONFIGS), elige la mejor por WAPE Ponderado en VAL,
    y solo promueve si supera al modelo activo (FAILSAFE).

    BLOQUEANTE: la operacion tarda varios minutos. Si el frontend
    necesita feedback inmediato, considerar version asincrona con
    BackgroundTasks (no implementada para simplicidad y para que la
    auditoria reciba la respuesta completa).
    """
    log_audit(
        admin["id"], "retrain_started",
        endpoint="/admin/retrain",
        ip_address=request.client.host if request.client else None,
        details={
            "include_staging": payload.include_staging_logs,
            "force_promote": payload.force_promote,
        },
    )

    inicio = datetime.now()
    try:
        resultado = ejecutar_reentrenamiento(
            user_id=admin["id"],
            include_staging=payload.include_staging_logs,
            notes=payload.notes,
            force_promote=payload.force_promote,
        )
    except Exception as e:
        log_audit(
            admin["id"], "retrain_failed",
            endpoint="/admin/retrain",
            details={"error": str(e)[:300]},
        )
        raise HTTPException(500, f"Reentrenamiento fallo: {type(e).__name__}: {e}")

    fin = datetime.now()
    duration = (fin - inicio).total_seconds()

    # Convertir configs_tested a schema
    configs_schema = []
    for c in resultado["configs_tested"]:
        if "error" in c:
            continue  # se exporta el detalle en el job pero no aqui
        configs_schema.append(RetrainConfigResult(
            config_name=c["config_name"],
            num_leaves=c["num_leaves"],
            learning_rate=c["learning_rate"],
            min_child_samples=c["min_child_samples"],
            lambda_l2=c["lambda_l2"],
            wape_ponderado_val=c["wape_ponderado_val"],
            wape_simple_val=c["wape_simple_val"],
            iterations_trained=c["iterations_trained"],
            duration_seconds=c["duration_seconds"],
        ))

    log_audit(
        admin["id"], "retrain_completed",
        endpoint="/admin/retrain",
        details={
            "job_id": resultado["job_id"],
            "decision": resultado["decision"],
            "best_wape_val": resultado["best_wape_val"],
            "current_wape_val": resultado["current_wape_val"],
        },
    )

    return RetrainResponse(
        job_id=resultado["job_id"],
        status="completed",
        started_at=inicio.isoformat(),
        finished_at=fin.isoformat(),
        duration_seconds=duration,
        configs_tested=configs_schema,
        best_config_name=resultado["best_config_name"],
        best_wape_val=resultado["best_wape_val"],
        current_wape_val=resultado["current_wape_val"],
        decision=resultado["decision"],
        new_model_version=resultado["new_model_version"],
        notes=payload.notes,
    )


@router.get("/retrain/jobs", summary="Historial de jobs de reentrenamiento")
async def listar_jobs(
    admin: Annotated[dict, Depends(require_roles("admin", "sinodal"))],
    limit: int = 20,
):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT j.*, u.username AS started_by_username
            FROM retrain_jobs j
            LEFT JOIN users u ON u.id = j.started_by
            ORDER BY j.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {"count": len(rows), "jobs": rows_to_list(rows)}


@router.get("/retrain/jobs/{job_id}", summary="Detalle de un job")
async def detalle_job(
    job_id: int,
    admin: Annotated[dict, Depends(require_roles("admin", "sinodal"))],
):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT j.*, u.username AS started_by_username
            FROM retrain_jobs j
            LEFT JOIN users u ON u.id = j.started_by
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Job no encontrado")
    return dict(row)


# ============================================================
# RESET - critico para sinodales
# ============================================================
@router.post(
    "/reset",
    response_model=ResetResponse,
    summary="Restaurar modelo M3 original (para pruebas de sinodales)",
)
async def reset_a_original(
    request: Request,
    user: Annotated[dict, Depends(require_roles("admin", "sinodal"))],
):
    """
    Restaura el modelo M3 original como modelo activo. Diseñado para
    que los sinodales puedan probar el sistema con el modelo entregado
    en la evaluacion del Freeze, sin importar cuantos reentrenes se
    hayan hecho.

    Mecanica:
      1. Marca todos los model_versions como is_active=0
      2. Marca la version con is_original=1 como is_active=1
      3. Registra el evento en audit_log

    Cualquier prediccion subsiguiente usara el modelo M3 hasta que el
    admin promueva otra version.
    """
    with get_connection() as conn:
        # Verificar que existe modelo original
        original = conn.execute(
            "SELECT * FROM model_versions WHERE is_original = 1 LIMIT 1"
        ).fetchone()
        if not original:
            raise HTTPException(
                500,
                "No hay modelo marcado como original. Corre scripts/seed_original_model.py",
            )

        # Capturar modelo activo previo
        prev = conn.execute(
            "SELECT version FROM model_versions WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        prev_version = prev["version"] if prev else None

        # Desactivar todos
        conn.execute("UPDATE model_versions SET is_active = 0")
        # Activar el original
        conn.execute(
            "UPDATE model_versions SET is_active = 1 WHERE id = ?",
            (original["id"],),
        )

    log_audit(
        user["id"], "model_reset_to_original",
        endpoint="/admin/reset",
        ip_address=request.client.host if request.client else None,
        details={"previous_active_version": prev_version},
    )

    return ResetResponse(
        reset_at=datetime.now().isoformat(),
        previous_active_version=prev_version,
        restored_version=original["version"],
        message=(
            "Modelo restaurado al original M3. Cualquier prediccion subsiguiente "
            "usara el modelo del Freeze entregado a los sinodales."
        ),
    )


# ============================================================
# Versiones del modelo
# ============================================================
@router.get(
    "/models",
    response_model=List[ModelVersionResponse],
    summary="Listar versiones del modelo",
)
async def listar_versiones(
    user: Annotated[dict, Depends(require_roles("admin", "sinodal", "manager"))],
):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM model_versions ORDER BY trained_at DESC"
        ).fetchall()
    return [
        ModelVersionResponse(
            id=r["id"],
            version=r["version"],
            is_active=bool(r["is_active"]),
            is_original=bool(r["is_original"]),
            wape_val=r["wape_val"],
            wape_test=r["wape_test"],
            config_name=r["config_name"],
            trained_at=r["trained_at"],
            promoted_at=r["promoted_at"],
            notes=r["notes"],
        ) for r in rows
    ]


@router.post(
    "/models/{version}/promote",
    summary="Promover una version especifica a modelo activo (admin)",
)
async def promover_version(
    version: str,
    payload: PromoteRequest,
    request: Request,
    admin: Annotated[dict, Depends(require_roles("admin"))],
):
    """
    Activa manualmente una version del modelo. Util para revertir a una
    version previa o promover un candidato sin pasar por retrain.
    """
    with get_connection() as conn:
        target = conn.execute(
            "SELECT * FROM model_versions WHERE version = ?", (version,)
        ).fetchone()
        if not target:
            raise HTTPException(404, f"Version '{version}' no encontrada")

        # Verificar que el archivo existe
        if not Path(target["path"]).exists():
            raise HTTPException(500, f"Archivo del modelo no existe: {target['path']}")

        prev = conn.execute(
            "SELECT version FROM model_versions WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        prev_version = prev["version"] if prev else None

        conn.execute("UPDATE model_versions SET is_active = 0")
        conn.execute(
            """
            UPDATE model_versions
            SET is_active = 1, promoted_at = datetime('now')
            WHERE version = ?
            """,
            (version,),
        )

    log_audit(
        admin["id"], "model_promoted",
        endpoint=f"/admin/models/{version}/promote",
        ip_address=request.client.host if request.client else None,
        details={
            "version": version, "previous_active": prev_version,
            "notes": payload.notes,
        },
    )
    return {
        "promoted_version": version,
        "previous_active": prev_version,
        "promoted_at": datetime.now().isoformat(),
    }


# ============================================================
# Audit log (consulta para sinodales)
# ============================================================
@router.get(
    "/audit-log",
    summary="Bitacora completa de auditoria (admin/sinodal)",
)
async def audit_log(
    user: Annotated[dict, Depends(require_roles("admin", "sinodal"))],
    limit: int = 100,
    action: str | None = None,
):
    """
    Consulta la bitacora de auditoria. Util para los sinodales: cada
    accion sensible (login, prediccion, firma, retrain, reset, promote)
    queda registrada con usuario, IP, fecha y detalles.
    """
    with get_connection() as conn:
        if action:
            rows = conn.execute(
                """
                SELECT a.*, u.username AS user_username
                FROM audit_log a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE a.action = ?
                ORDER BY a.timestamp DESC
                LIMIT ?
                """,
                (action, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT a.*, u.username AS user_username
                FROM audit_log a
                LEFT JOIN users u ON u.id = a.user_id
                ORDER BY a.timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return {"count": len(rows), "entries": rows_to_list(rows)}
