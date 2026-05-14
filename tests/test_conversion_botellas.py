# ============================================================
# tests/test_conversion_botellas.py
# ============================================================

from services.conversion_botellas import (
    detectar_tipo_servicio, unidades_a_botellas,
)


def test_detecta_sencillo():
    assert detectar_tipo_servicio("CUERVO 1800 REP (Sencillo)") == "sencillo"


def test_detecta_doble():
    assert detectar_tipo_servicio("CHIVAS 12 (Doble)") == "doble"


def test_detecta_botella():
    assert detectar_tipo_servicio("ABSOLUT (Botella)") == "botella"


def test_detecta_preparado():
    assert detectar_tipo_servicio("MICHELADA (Preparado)") == "preparado"


def test_sin_parentesis_es_otro():
    assert detectar_tipo_servicio("REFRESCO COCA COLA 600ml") == "otro"


def test_conversion_cero_unidades():
    assert unidades_a_botellas(0, "X (Sencillo)") == 0


def test_conversion_sencillo_redondea_hacia_arriba():
    # 17 sencillos = 25.5 oz, requiere 1 botella + algo -> 2 botellas
    result = unidades_a_botellas(17, "RON (Sencillo)")
    assert result == 2  # techo de 1.005...


def test_conversion_botella_directa():
    assert unidades_a_botellas(5, "ABSOLUT (Botella)") == 5


def test_conversion_doble():
    # 8 dobles = 24 oz, requiere 1 botella (24/25.36 = 0.946, techo=1)
    assert unidades_a_botellas(8, "RON (Doble)") == 1


def test_conversion_negativa_es_cero():
    assert unidades_a_botellas(-5, "RON (Sencillo)") == 0
