# ============================================================
# tests/test_auth.py - Tests de autenticacion y autorizacion
# ============================================================

import pytest


def test_login_credenciales_invalidas_devuelve_401(client):
    r = client.post(
        "/auth/login",
        data={"username": "noexiste", "password": "WrongPass!"},
    )
    assert r.status_code == 401


def test_crear_usuario_y_login(client):
    # Crear admin directamente via auth.py para tener punto de entrada
    from auth import create_user
    create_user("admin_test", "Admin12345!", "admin", "Admin Test")

    r = client.post(
        "/auth/login",
        data={"username": "admin_test", "password": "Admin12345!"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body["role"] == "admin"
    assert body["username"] == "admin_test"


def test_me_requiere_token(client):
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_con_token_valido(client, admin_token):
    r = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_endpoint_admin_solo_admin(client, manager_token):
    """Un manager no puede crear usuarios (es endpoint admin-only)."""
    r = client.post(
        "/auth/users",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "username": "x", "password": "Test12345!",
            "role": "barman", "full_name": "x",
        },
    )
    assert r.status_code == 403


def test_endpoint_admin_acepta_admin(client, admin_token):
    r = client.post(
        "/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "nuevo_barman", "password": "Test12345!",
            "role": "barman", "full_name": "Nuevo Barman",
        },
    )
    assert r.status_code == 201
    assert r.json()["role"] == "barman"


def test_rol_invalido_falla(client, admin_token):
    r = client.post(
        "/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "x", "password": "Test12345!",
            "role": "supervisor",  # no existe
            "full_name": "x",
        },
    )
    # Pydantic rechaza antes de llegar al handler
    assert r.status_code == 422
