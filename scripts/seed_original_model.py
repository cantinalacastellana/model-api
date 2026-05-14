# ============================================================
# scripts/seed_original_model.py
# ------------------------------------------------------------
# Registra el modelo M3 (lgbm_v1.joblib) del artefacto entregado
# en M3 como version 'original' y lo marca como is_active=1.
#
# Este script es la "semilla" del sistema: sin correrlo, la API
# no tiene modelo activo y todos los /predict caen al baseline.
#
# Tambien es el target del endpoint POST /admin/reset: cuando los
# sinodales invocan reset, el sistema vuelve a la version 'original'
# registrada aqui.
#
# Pasos previos:
#   1. Copiar lgbm_v1.joblib del artefacto a data/models/original/
#   2. Copiar df_ml_ready_for_M3.csv a data/models/original/
#
# Uso: python scripts/seed_original_model.py
# ============================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings
from database import init_database, get_connection


# WAPE Ponderado en validacion del modelo M3 (del entregable)
# Documentado en la Seccion 5 del PDF de Jose Emilio.
WAPE_VAL_M3 = 109.66
WAPE_TEST_M3 = 116.31


def main():
    settings = get_settings()
    init_database()

    model_file = settings.MODEL_ORIGINAL_DIR / settings.MODEL_FILENAME
    snapshot_file = settings.MODEL_ORIGINAL_DIR / settings.SNAPSHOT_ORIGINAL_FILENAME

    print("=" * 60)
    print("Registrando modelo M3 original")
    print("=" * 60)

    if not model_file.exists():
        print(f"ERROR: No existe {model_file}")
        print("       Copia lgbm_v1.joblib del artefacto a data/models/original/")
        sys.exit(1)

    if not snapshot_file.exists():
        print(f"ADVERTENCIA: No existe el snapshot {snapshot_file}")
        print("             /predict sin CSV no funcionara hasta que lo coloques.")

    version_name = "original_M3"

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM model_versions WHERE version = ?", (version_name,)
        ).fetchone()
        if existing:
            print(f"\nYa existe la version '{version_name}'. Reactivando...")
            conn.execute("UPDATE model_versions SET is_active = 0")
            conn.execute(
                """UPDATE model_versions SET is_active = 1, is_original = 1
                   WHERE version = ?""",
                (version_name,),
            )
        else:
            print(f"\nRegistrando '{version_name}'...")
            # Desactivar cualquier otro modelo
            conn.execute("UPDATE model_versions SET is_active = 0")
            conn.execute(
                """
                INSERT INTO model_versions (
                    version, path, is_active, is_original,
                    wape_val, wape_test, config_name, config_params,
                    promoted_at, notes
                ) VALUES (?, ?, 1, 1, ?, ?, ?, ?, datetime('now'), ?)
                """,
                (
                    version_name, str(model_file),
                    WAPE_VAL_M3, WAPE_TEST_M3,
                    "M3_freeze",
                    '{"source": "Entregable M3 - Jose Emilio Kuri Otero", "freeze_date": "2026-04-13"}',
                    (
                        "Modelo entregado en M3 del Proyecto de Aplicacion de Ciencia "
                        "de Datos (Maestria UP, Primavera 2026). Calificado por el "
                        "Prof. Luis Fernando Lupian Sanchez. Target del endpoint "
                        "/admin/reset para que los sinodales puedan restaurarlo."
                    ),
                ),
            )

    print("\n" + "=" * 60)
    print(f"OK. Modelo activo: {version_name}")
    print(f"    WAPE VAL: {WAPE_VAL_M3}%  |  WAPE TEST: {WAPE_TEST_M3}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
