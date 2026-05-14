# ============================================================
# auth.py - Autenticacion JWT + manejo de usuarios y roles
# ------------------------------------------------------------
# Roles:
#   admin    -> data scientist; acceso completo (retrain, reset,
#               promote, todos los endpoints administrativos)
#   manager  -> gerente; predice, descarga PDFs, firma ordenes,
#               registra ventas, ve metricas, consulta drift
#   barman   -> registra notas a barra durante la jornada
#   sinodal  -> read-only; ve modelo activo, predicciones, logs
#               y puede invocar /reset para volver al modelo M3
#
# Las contrasenas se guardan con bcrypt. Los JWT expiran a las 8h
# para cubrir una jornada laboral.
# ============================================================

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from config import get_settings
from database import get_connection, log_audit, row_to_dict


# bcrypt context. rounds=12 es un balance estandar entre seguridad y CPU.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# OAuth2 scheme - el frontend manda el token en Authorization: Bearer <token>
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


# ------------------------------------------------------------
# Hashing de contrasenas
# ------------------------------------------------------------
def hash_password(plain_password: str) -> str:
    """Hashea una contrasena con bcrypt."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica una contrasena contra su hash bcrypt."""
    return pwd_context.verify(plain_password, hashed_password)


# ------------------------------------------------------------
# JWT - creacion y verificacion
# ------------------------------------------------------------
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Crea un JWT firmado. El payload incluye:
      sub  -> username
      uid  -> user id (int)
      role -> rol del usuario
      exp  -> expiracion (UTC)
    """
    settings = get_settings()
    to_encode = data.copy()
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decodifica y verifica el JWT. Lanza HTTPException si falla."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token invalido o expirado: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ------------------------------------------------------------
# Acceso a usuarios en BD
# ------------------------------------------------------------
def get_user_by_username(username: str) -> dict | None:
    """Busca un usuario activo por username."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()
        return row_to_dict(row)


def get_user_by_id(user_id: int) -> dict | None:
    """Busca un usuario activo por id."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        return row_to_dict(row)


def authenticate_user(username: str, password: str) -> dict | None:
    """
    Verifica credenciales. Retorna el dict del usuario si es valido,
    None si no. Actualiza last_login_at en exito.
    """
    user = get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    # Actualizar ultimo login
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
            (user["id"],),
        )
    return user


def create_user(username: str, password: str, role: str, full_name: str = "") -> int:
    """
    Crea un usuario. Lanza ValueError si el rol no es valido o si el
    username ya existe.
    """
    if role not in ("admin", "manager", "barman", "sinodal"):
        raise ValueError(f"Rol invalido: {role}")
    pw_hash = hash_password(password)
    with get_connection() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO users (username, password_hash, role, full_name)
                VALUES (?, ?, ?, ?)
                """,
                (username, pw_hash, role, full_name),
            )
            return cur.lastrowid
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise ValueError(f"El usuario '{username}' ya existe")
            raise


# ------------------------------------------------------------
# Dependencias FastAPI para proteger endpoints
# ------------------------------------------------------------
async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    """
    Dependencia que extrae el usuario actual del JWT. Si el token es
    invalido o el usuario fue desactivado, retorna 401.
    """
    payload = decode_token(token)
    user_id = payload.get("uid")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sin uid",
        )
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
        )
    return user


def require_roles(*allowed_roles: str):
    """
    Factory de dependencias para proteger endpoints por rol.
    Uso:
        @router.post("/retrain", dependencies=[Depends(require_roles("admin"))])
    """
    async def _checker(user: Annotated[dict, Depends(get_current_user)]) -> dict:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Acceso denegado. Tu rol '{user['role']}' no tiene permiso "
                    f"para este endpoint (requiere: {', '.join(allowed_roles)})"
                ),
            )
        return user
    return _checker
