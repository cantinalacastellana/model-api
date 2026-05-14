# ============================================================
# tests/conftest.py - Fixtures globales de pytest
# ============================================================
import os
import sys
from pathlib import Path
import tempfile
import pytest

# Permitir imports desde la raiz
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """
    Antes de cualquier test, redirigir la BD a un archivo temporal
    para no tocar la BD real.
    """
    tmpdir = tempfile.mkdtemp(prefix="castellana_test_")
    os.environ["DB_PATH"] = str(Path(tmpdir) / "test.db")
    os.environ["JWT_SECRET_KEY"] = "test-secret-do-not-use-in-prod"
    os.environ["OPENAI_API_KEY"] = ""  # forzar fallback silencioso
    yield
    # No borrar tmpdir; util para inspeccionar despues


@pytest.fixture
def fresh_db():
    """Crea BD limpia con esquema."""
    from database import init_database, get_db_path
    db = get_db_path()
    if db.exists():
        db.unlink()
    init_database()
    yield


@pytest.fixture
def admin_token(fresh_db):
    """Crea un admin y retorna un JWT valido."""
    from auth import create_user, create_access_token
    uid = create_user("test_admin", "TestPass123!", "admin", "Test Admin")
    return create_access_token({"sub": "test_admin", "uid": uid, "role": "admin"})


@pytest.fixture
def manager_token(fresh_db):
    from auth import create_user, create_access_token
    uid = create_user("test_manager", "TestPass123!", "manager", "Test Manager")
    return create_access_token({"sub": "test_manager", "uid": uid, "role": "manager"})


@pytest.fixture
def barman_token(fresh_db):
    from auth import create_user, create_access_token
    uid = create_user("test_barman", "TestPass123!", "barman", "Test Barman")
    return create_access_token({"sub": "test_barman", "uid": uid, "role": "barman"})


@pytest.fixture
def sinodal_token(fresh_db):
    from auth import create_user, create_access_token
    uid = create_user("test_sinodal", "TestPass123!", "sinodal", "Test Sinodal")
    return create_access_token({"sub": "test_sinodal", "uid": uid, "role": "sinodal"})


@pytest.fixture
def client(fresh_db):
    """Cliente FastAPI para tests de integracion."""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)
