# ============================================================
# scripts/init_db.py
# ------------------------------------------------------------
# Inicializa la BD y crea el primer usuario admin de forma
# interactiva. Correr una sola vez al desplegar:
#
#   python scripts/init_db.py
# ============================================================

import sys
import getpass
from pathlib import Path

# Permitir imports relativos desde la raiz
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import init_database, get_connection
from auth import create_user


def main():
    print("=" * 60)
    print("Inicializacion de la BD - API Cantina La Castellana")
    print("=" * 60)

    print("\n[1/3] Creando esquema SQLite...")
    init_database()
    print("    OK")

    # Verificar si ya hay usuarios
    with get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    if n > 0:
        print(f"\n[2/3] Ya hay {n} usuario(s) registrados. No se crea admin.")
        print("    Si necesitas resetear, borra la BD manualmente y vuelve a correr.")
        return

    print("\n[2/3] Creando primer usuario admin...")
    print("    Este usuario tendra acceso completo a la API.")
    while True:
        username = input("    Username (min 3 caracteres): ").strip()
        if len(username) >= 3:
            break
        print("    Username muy corto.")

    full_name = input("    Nombre completo (opcional): ").strip()

    while True:
        password = getpass.getpass("    Password (min 8 caracteres): ")
        if len(password) < 8:
            print("    Password muy corto.")
            continue
        confirm = getpass.getpass("    Confirma password: ")
        if password != confirm:
            print("    No coinciden.")
            continue
        break

    try:
        user_id = create_user(username, password, "admin", full_name)
        print(f"\n[3/3] Usuario admin creado con id={user_id}: {username}")
    except ValueError as e:
        print(f"    ERROR: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Listo. Proximos pasos:")
    print("  1. Coloca lgbm_v1.joblib y df_ml_ready_for_M3.csv en")
    print("     data/models/original/")
    print("  2. Corre: python scripts/seed_original_model.py")
    print("  3. Levanta la API: uvicorn main:app --port 8001")
    print("=" * 60)


if __name__ == "__main__":
    main()
