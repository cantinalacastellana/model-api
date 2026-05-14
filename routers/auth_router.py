# ============================================================
# routers/auth_router.py
# ------------------------------------------------------------
# Endpoints de autenticacion:
#   POST /auth/login            - login con username/password, devuelve JWT
#   GET  /auth/me               - info del usuario actual
#   POST /auth/users            - crear usuario (solo admin)
#   GET  /auth/users            - listar usuarios (solo admin)
# ============================================================

from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm

from auth import (
    authenticate_user, create_access_token, create_user,
    get_current_user, require_roles,
)
from config import get_settings
from database import get_connection, log_audit, rows_to_list
from schemas.auth import (
    TokenResponse, UserCreateRequest, UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login con username/password (form-data, compatible OAuth2)",
)
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """
    Endpoint estandar OAuth2 password flow. El frontend envia
    form-urlencoded con username/password. Retorna JWT de 8 horas.
    """
    settings = get_settings()
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        log_audit(
            None, "login_failed",
            endpoint="/auth/login",
            ip_address=request.client.host if request.client else None,
            details={"username": form_data.username},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
        )

    token = create_access_token({
        "sub": user["username"], "uid": user["id"], "role": user["role"],
    })
    log_audit(
        user["id"], "login_success",
        endpoint="/auth/login",
        ip_address=request.client.host if request.client else None,
    )
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        full_name=user.get("full_name"),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Datos del usuario autenticado",
)
async def whoami(user: Annotated[dict, Depends(get_current_user)]):
    return UserResponse(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        full_name=user.get("full_name"),
        is_active=bool(user["is_active"]),
        created_at=user["created_at"],
        last_login_at=user.get("last_login_at"),
    )


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear usuario (solo rol admin)",
)
async def crear_usuario(
    payload: UserCreateRequest,
    request: Request,
    admin: Annotated[dict, Depends(require_roles("admin"))],
):
    try:
        new_id = create_user(
            payload.username, payload.password, payload.role, payload.full_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_audit(
        admin["id"], "user_created",
        endpoint="/auth/users",
        ip_address=request.client.host if request.client else None,
        details={"new_user_id": new_id, "username": payload.username, "role": payload.role},
    )

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (new_id,)).fetchone()

    return UserResponse(
        id=row["id"], username=row["username"], role=row["role"],
        full_name=row["full_name"], is_active=bool(row["is_active"]),
        created_at=row["created_at"], last_login_at=row["last_login_at"],
    )


@router.get(
    "/users",
    response_model=List[UserResponse],
    summary="Listar usuarios (solo rol admin)",
)
async def listar_usuarios(
    admin: Annotated[dict, Depends(require_roles("admin"))],
):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [
        UserResponse(
            id=r["id"], username=r["username"], role=r["role"],
            full_name=r["full_name"], is_active=bool(r["is_active"]),
            created_at=r["created_at"], last_login_at=r["last_login_at"],
        ) for r in rows
    ]
