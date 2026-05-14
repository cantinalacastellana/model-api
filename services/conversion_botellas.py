# ============================================================
# services/conversion_botellas.py - Conversion tragos -> botellas
# ------------------------------------------------------------
# Reglas del M3 (Seccion 5: Decision de Modelo y Regla de Uso):
#   sencillo  = 1.5 oz
#   doble     = 3.0 oz
#   botella   = 25.36 oz (~ 17 sencillos)
#
# Cada SKU del POS tiene una receta documentada. La prediccion
# llega en unidades vendidas; aqui se convierte al numero entero
# de botellas necesario para cubrir esa demanda.
#
# Para SKUs que no son licor (refrescos, cerveza, comida), el SKU
# ya viene en unidades de servicio directo y no requiere conversion.
# ============================================================

import re
from typing import Literal

OZ_POR_SENCILLO = 1.5
OZ_POR_DOBLE = 3.0
OZ_POR_BOTELLA = 25.36


# Detecta el tipo de servicio desde el nombre del SKU.
# Convencion del POS: "CUERVO 1800 REP (Sencillo)", "ABSOLUT (Botella)", etc.
PATRON_TIPO = re.compile(r"\(([^)]+)\)\s*$", re.IGNORECASE)


def detectar_tipo_servicio(sku_nombre: str) -> Literal["sencillo", "doble", "botella", "preparado", "otro"]:
    """
    Extrae el tipo de servicio del nombre del SKU.
    Si no aparece entre parentesis, retorna 'otro' (sin conversion).
    """
    m = PATRON_TIPO.search(sku_nombre)
    if not m:
        return "otro"
    tipo = m.group(1).strip().lower()
    if tipo in ("sencillo",):
        return "sencillo"
    if tipo in ("doble",):
        return "doble"
    if tipo in ("botella",):
        return "botella"
    if tipo in ("preparado",):
        return "preparado"
    return "otro"


def unidades_a_botellas(unidades: float, sku_nombre: str) -> int:
    """
    Convierte unidades de prediccion a botellas fisicas a bajar.

    - Sencillo: cada botella cubre ~17 sencillos
    - Doble: cada botella cubre ~8 dobles
    - Botella: 1 unidad = 1 botella
    - Preparado/Otro: se considera 1:1 (no conversion)

    El redondeo es hacia arriba: siempre conviene bajar de mas a barra
    que quedarse corto (recuerda: el objetivo es minimizar notas a barra,
    no minimizar sobrante).
    """
    if unidades <= 0:
        return 0
    tipo = detectar_tipo_servicio(sku_nombre)
    if tipo == "sencillo":
        oz_total = unidades * OZ_POR_SENCILLO
        botellas = oz_total / OZ_POR_BOTELLA
    elif tipo == "doble":
        oz_total = unidades * OZ_POR_DOBLE
        botellas = oz_total / OZ_POR_BOTELLA
    elif tipo == "botella":
        botellas = unidades
    else:
        # preparado/otro: 1:1
        botellas = unidades

    # Redondeo hacia arriba (techo)
    import math
    return int(math.ceil(botellas))
