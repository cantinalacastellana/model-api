# ============================================================
# tests/test_endpoints.py - Tests de integracion
# ------------------------------------------------------------
# Tests "smoke" para los endpoints principales. NO requieren tener
# el modelo entrenado o el snapshot original. Validan contratos
# (codigos HTTP, schemas de respuesta, autorizacion).
# ============================================================

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_devuelve_links(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "docs" in r.json()


def test_nota_barra_requiere_login(client):
    r = client.post(
        "/logs/nota-barra",
        json={"nota_date": "2026-05-13", "sku": "X", "quantity": 1},
    )
    assert r.status_code == 401


def test_nota_barra_barman_puede_registrar(client, barman_token):
    r = client.post(
        "/logs/nota-barra",
        headers={"Authorization": f"Bearer {barman_token}"},
        json={
            "nota_date": "2026-05-13",
            "sku": "RON BACARDI (Sencillo)",
            "quantity": 2,
            "bloque_horario": "DEMANDA MEDIA",
            "reason": "Se acabo en barra",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["sku"] == "RON BACARDI (Sencillo)"
    assert body["quantity"] == 2


def test_sinodal_no_puede_registrar_nota(client, sinodal_token):
    """Sinodal es read-only excepto para /admin/reset."""
    r = client.post(
        "/logs/nota-barra",
        headers={"Authorization": f"Bearer {sinodal_token}"},
        json={"nota_date": "2026-05-13", "sku": "X", "quantity": 1},
    )
    assert r.status_code == 403


def test_metrics_dashboard_devuelve_estructura(client, admin_token):
    r = client.get(
        "/metrics/dashboard",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "adoption" in body
    assert "impact" in body
    assert "wape" in body


def test_drift_status_sin_chequeos_responde_ok(client, admin_token):
    r = client.get(
        "/drift/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["last_check_date"] is None
    assert body["alert_triggered"] is False


def test_reset_sin_modelo_original_falla(client, admin_token):
    """Si no se ha hecho seed_original_model.py, /admin/reset falla."""
    r = client.post(
        "/admin/reset",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 500


def test_reset_con_modelo_original_restaura(client, admin_token):
    """Tras registrar un modelo como original, /admin/reset funciona."""
    from database import get_connection
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO model_versions
               (version, path, is_active, is_original, wape_val, config_name)
               VALUES (?, ?, 1, 1, ?, ?)""",
            ("original_M3", "/tmp/fake_path.joblib", 109.66, "M3_freeze"),
        )

    r = client.post(
        "/admin/reset",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["restored_version"] == "original_M3"


def test_reset_accesible_por_sinodal(client, sinodal_token):
    """CRITICO: el sinodal debe poder invocar reset para sus pruebas."""
    from database import get_connection
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO model_versions
               (version, path, is_active, is_original, wape_val, config_name)
               VALUES (?, ?, 1, 1, ?, ?)""",
            ("original_M3", "/tmp/fake_path.joblib", 109.66, "M3_freeze"),
        )

    r = client.post(
        "/admin/reset",
        headers={"Authorization": f"Bearer {sinodal_token}"},
    )
    assert r.status_code == 200


def test_audit_log_registra_eventos(client, admin_token):
    """Verifica que las acciones quedan registradas (evidencia auditoria Tier A)."""
    # Hacer un login para generar evento
    from database import get_connection
    with get_connection() as conn:
        n_before = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log"
        ).fetchone()["c"]

    # Crear usuario via endpoint admin
    client.post(
        "/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "test_audit", "password": "Test12345!",
            "role": "manager", "full_name": "Audit",
        },
    )

    with get_connection() as conn:
        n_after = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log"
        ).fetchone()["c"]
    # Debe haberse incrementado al menos 1 (user_created)
    assert n_after > n_before


def test_csv_no_existe_devuelve_404(client, admin_token):
    r = client.get(
        "/predict/2026-05-13/csv",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404
