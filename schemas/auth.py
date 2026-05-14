# ============================================================
# schemas/auth.py - Modelos Pydantic de autenticacion
# ============================================================

from pydantic import BaseModel, Field
from typing import Literal


class LoginRequest(BaseModel):
    """Payload del login (compatible con OAuth2PasswordRequestForm)."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)


class TokenResponse(BaseModel):
    """Respuesta del login: el JWT y metadatos del usuario."""
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user_id: int
    username: str
    role: Literal["admin", "manager", "barman", "sinodal"]
    full_name: str | None = None


class UserCreateRequest(BaseModel):
    """Payload para crear un usuario (endpoint solo-admin)."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["admin", "manager", "barman", "sinodal"]
    full_name: str = Field("", max_length=120)


class UserResponse(BaseModel):
    """Usuario (sin password_hash)."""
    id: int
    username: str
    role: str
    full_name: str | None
    is_active: bool
    created_at: str
    last_login_at: str | None
