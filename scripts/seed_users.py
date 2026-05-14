# ============================================================
# scripts/seed_users.py
# ------------------------------------------------------------
# Crea usuarios de PRUEBA con cada rol. Util para que los
# sinodales puedan probar la API rapidamente con cada rol sin
# tener que crear usuarios uno por uno.
#
# IMPORTANTE: en produccion real, NO usar estos passwords; crear
# usuarios reales con init_db.py + endpoint POST /auth/users.
#
# Uso: python scripts/seed_users.py
# ============================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import init_database, get_connection
from auth import create_user


USUARIOS_PRUEBA = [
    # username, password, rol, full_name
    ("admin_demo",    "Admin2026!Demo",   "admin",   "Admin de Prueba (Data Scientist)"),
    ("gerente_demo",  "Gerente2026!Demo", "manager", "Gerente de Prueba (Cantina)"),
    ("barman_demo",   "Barman2026!Demo",  "barman",  "Barman de Prueba"),
    ("sinodal_demo",  "Sinodal2026!Demo", "sinodal", "Sinodal Evaluador"),
]


def main():
    init_database()
    print("=" * 60)
    print("Creando usuarios de PRUEBA (no usar en produccion real)")
    print("=" * 60)

    for username, password, role, full_name in USUARIOS_PRUEBA:
        try:
            uid = create_user(username, password, role, full_name)
            print(f"  [{role:8}] {username} -> id={uid}, password='{password}'")
        except ValueError as e:
            print(f"  [{role:8}] {username} -> SKIP ({e})")

    print("\n" + "=" * 60)
    print("Credenciales para los sinodales:")
    print("  POST /model/auth/login")
    for u, p, r, _ in USUARIOS_PRUEBA:
        print(f"    {r:8} -> username={u}, password={p}")
    print("=" * 60)


if __name__ == "__main__":
    main()
