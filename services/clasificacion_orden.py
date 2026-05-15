# ============================================================
# services/clasificacion_orden.py
# ------------------------------------------------------------
# Clasifica las predicciones del modelo en los buckets que
# usa el formato de orden del artefacto M3 de Jose Emilio.
#
# LOGICA CLAVE: el artefacto asume que cada SKU ya tiene
# 1 BOTELLA EN BARRA al inicio del dia. Por lo tanto, solo
# se reportan botellas ADICIONALES a bajar de bodega, NUNCA
# el total absoluto. Esto es muy diferente al output crudo del
# modelo (que predice volumen total esperado).
#
# Buckets del PDF original:
#   1. extra_hoy:          SKUs que requieren >1 botella
#                          (la primera ya esta en barra, se baja el resto)
#   2. restock_inminente:  SKUs con consumo predicho >= 75% de 1 botella
#                          (no se baja extra, pero alerta de tenerlo a mano)
#   3. surtir_minimo:      SKUs con demanda baja (1 botella basta)
#                          - listado simple por categoria
#   4. tragos_sueltos:     SKUs que no son licor de botella
#                          (cocteles, cervezas, refrescos, michelados)
#                          - se reportan en unidades, no botellas
# ============================================================

from __future__ import annotations
from typing import TypedDict
import re

from services.conversion_botellas import (
    OZ_POR_SENCILLO, OZ_POR_DOBLE, OZ_POR_BOTELLA,
    detectar_tipo_servicio,
)


# Umbral para alerta de "restock inminente" (% de consumo de 1 botella)
UMBRAL_RESTOCK_INMINENTE = 0.75

# Categorias del POS que NO son licor por botella -> tragos sueltos
# (sus pred_final son en unidades de venta, no en oz)
CATEGORIAS_TRAGO_SUELTO = {
    "Cocteles", "Coctel", "Cocteleria",
    "Cerveza", "Cervezas",
    "Refrescos", "Refresco",
    "Aguas", "Agua",
    "Sin Alcohol",
    "Varios",
    "Botana", "Botanas", "Comida",
}


# ============================================================
# Tipos
# ============================================================
class ItemExtra(TypedDict):
    sku: str
    sku_base: str        # nombre sin "(Sencillo)" - para mostrar limpio
    categoria: str
    botellas_extra: int  # botellas ADICIONALES a bajar (no incluye la 1 base)
    detalle: str         # "Venta directa de botella + 27 Sencillo"
    pred_oz_total: float


class ItemRestock(TypedDict):
    sku: str
    categoria: str
    pred_final: int
    oz_consumidas: float
    pct_botella: float   # 0.0 - 1.0


class ItemTragoSuelto(TypedDict):
    sku: str
    sku_base: str
    categoria: str
    unidades: int


class ClasificacionOrden(TypedDict):
    extra_hoy: list[ItemExtra]
    restock_inminente: list[ItemRestock]
    surtir_minimo: dict[str, list[str]]   # categoria -> [sku_base ordenado]
    tragos_sueltos: dict[str, list[ItemTragoSuelto]]  # categoria -> items
    total_botellas_extra: int
    total_tragos_sueltos: int


# ============================================================
# Helpers
# ============================================================
_PATRON_TIPO = re.compile(r"\s*\(([^)]+)\)\s*$")


def _limpiar_nombre_sku(sku: str) -> str:
    """'BACARDI BCO (Sencillo)' -> 'BACARDI BCO'"""
    return _PATRON_TIPO.sub("", sku).strip()


def _agrupar_servicios_del_mismo_sku(
    predictions: list[dict],
) -> dict[str, dict[str, dict]]:
    """
    Agrupa predicciones por SKU base. Devuelve:
        {sku_base: {tipo_servicio: prediccion}}
    Ej: {"BACARDI BCO": {"sencillo": {...}, "doble": {...}, "botella": {...}}}

    Esto es necesario porque un mismo licor puede tener filas separadas
    por tipo de servicio en el POS (cada una con su propia prediccion).
    """
    grupos: dict[str, dict[str, dict]] = {}
    for p in predictions:
        sku = p["sku"]
        tipo = detectar_tipo_servicio(sku)
        base = _limpiar_nombre_sku(sku)
        if base not in grupos:
            grupos[base] = {}
        grupos[base][tipo] = p
    return grupos


def _es_categoria_tragos_sueltos(categoria: str | None) -> bool:
    """¿Esta categoria se reporta en unidades sueltas en vez de botellas?"""
    if not categoria:
        return False
    return categoria.strip() in CATEGORIAS_TRAGO_SUELTO


def _detalle_servicios(servicios: dict[str, dict]) -> str:
    """
    Construye string descriptivo de los servicios predichos para un licor.
    Ej: "Venta directa de botella + 27 Sencillo"
        "3 Doble, 17 Sencillo"
    """
    partes: list[str] = []
    # Orden: botella, doble, sencillo
    if "botella" in servicios and servicios["botella"]["pred_final"] > 0:
        n = int(servicios["botella"]["pred_final"])
        partes.append(f"Venta directa de botella" + (f" x{n}" if n > 1 else ""))
    if "doble" in servicios and servicios["doble"]["pred_final"] > 0:
        n = int(servicios["doble"]["pred_final"])
        partes.append(f"{n} Doble")
    if "sencillo" in servicios and servicios["sencillo"]["pred_final"] > 0:
        n = int(servicios["sencillo"]["pred_final"])
        partes.append(f"{n} Sencillo")
    if "preparado" in servicios and servicios["preparado"]["pred_final"] > 0:
        n = int(servicios["preparado"]["pred_final"])
        partes.append(f"{n} Preparado")
    return ", ".join(partes) if partes else "-"


def _oz_predichas_para_servicio(tipo: str, pred_final: int) -> float:
    """
    Convierte una prediccion a OZ de licor consumidas.
    Solo aplica a servicios por trago (sencillo/doble). Botella entera
    se cuenta como botellas, no oz.
    """
    if tipo == "sencillo":
        return pred_final * OZ_POR_SENCILLO
    if tipo == "doble":
        return pred_final * OZ_POR_DOBLE
    return 0.0


# ============================================================
# Funcion principal
# ============================================================
def clasificar_para_orden(predictions: list[dict]) -> ClasificacionOrden:
    """
    Toma la lista de SKU predictions y la clasifica en los 4 buckets
    del formato M3 original.

    Cada prediction debe tener al menos:
        - sku (str)
        - categoria (str | None)
        - pred_final (int)
        - guardrails_applied (list)  -- opcional
    """
    extra_hoy: list[ItemExtra] = []
    restock_inminente: list[ItemRestock] = []
    surtir_minimo: dict[str, list[str]] = {}
    tragos_sueltos: dict[str, list[ItemTragoSuelto]] = {}

    grupos = _agrupar_servicios_del_mismo_sku(predictions)

    for sku_base, servicios in grupos.items():
        # Categoria: tomar la primera no-nula entre los servicios
        categoria = next(
            (s.get("categoria") for s in servicios.values() if s.get("categoria")),
            "Sin categoria",
        )

        # --- TRAGOS SUELTOS (categoria no es de licor) ---
        if _es_categoria_tragos_sueltos(categoria):
            unidades_total = sum(
                int(s.get("pred_final", 0)) for s in servicios.values()
            )
            if unidades_total > 0:
                tragos_sueltos.setdefault(categoria, []).append({
                    "sku": list(servicios.values())[0]["sku"],
                    "sku_base": sku_base,
                    "categoria": categoria,
                    "unidades": unidades_total,
                })
            continue

        # --- LICORES (sencillo/doble/botella) ---
        # 1. Calcular consumo total en oz para los servicios de trago
        oz_total = 0.0
        for tipo, pred in servicios.items():
            oz_total += _oz_predichas_para_servicio(tipo, int(pred["pred_final"]))

        # 2. Venta directa de botella (suma aparte)
        botellas_venta_directa = int(
            servicios.get("botella", {}).get("pred_final", 0) or 0
        )

        # 3. Botellas necesarias para cubrir el consumo de trago
        botellas_necesarias_para_trago = (
            int(oz_total // OZ_POR_BOTELLA) + (1 if oz_total % OZ_POR_BOTELLA > 0 else 0)
        )
        # Pct de la 1a botella que se consumiria (para alerta)
        pct_botella = oz_total / OZ_POR_BOTELLA if OZ_POR_BOTELLA > 0 else 0.0

        # 4. Botellas TOTAL a tener disponibles = trago + venta directa
        botellas_total = botellas_necesarias_para_trago + botellas_venta_directa
        # 5. Botellas EXTRA a bajar = total - 1 (la primera ya esta en barra)
        botellas_extra = max(0, botellas_total - 1)

        # ---- Clasificacion ----
        if botellas_extra > 0:
            # EXTRA HOY: hay que bajar botellas adicionales
            extra_hoy.append({
                "sku": list(servicios.values())[0]["sku"],
                "sku_base": sku_base,
                "categoria": categoria,
                "botellas_extra": botellas_extra,
                "detalle": _detalle_servicios(servicios),
                "pred_oz_total": oz_total,
            })
        elif pct_botella >= UMBRAL_RESTOCK_INMINENTE:
            # RESTOCK INMINENTE: cerca de consumir la 1a botella
            # Tomar el servicio con mas trago (el que dispara la alerta)
            tipo_dominante = max(
                ("sencillo", "doble"),
                key=lambda t: _oz_predichas_para_servicio(
                    t, int(servicios.get(t, {}).get("pred_final", 0) or 0)
                ),
            )
            pred_dominante = servicios.get(tipo_dominante, {})
            if pred_dominante.get("pred_final"):
                restock_inminente.append({
                    "sku": pred_dominante["sku"],
                    "categoria": categoria,
                    "pred_final": int(pred_dominante["pred_final"]),
                    "oz_consumidas": round(oz_total, 1),
                    "pct_botella": round(pct_botella, 2),
                })
        else:
            # SURTIDO MINIMO: 1 botella basta, listado por categoria
            # (Solo agregar si hubo alguna prediccion - sino no aparece)
            if any(int(s.get("pred_final", 0) or 0) > 0 for s in servicios.values()):
                surtir_minimo.setdefault(categoria, []).append(sku_base)

    # --- Ordenamiento ---
    extra_hoy.sort(key=lambda x: (x["categoria"], -x["botellas_extra"]))
    restock_inminente.sort(key=lambda x: -x["pct_botella"])
    for cat in surtir_minimo:
        surtir_minimo[cat].sort()
    for cat in tragos_sueltos:
        tragos_sueltos[cat].sort(key=lambda x: x["sku_base"])

    total_botellas_extra = sum(item["botellas_extra"] for item in extra_hoy)
    total_tragos_sueltos = sum(
        item["unidades"]
        for items in tragos_sueltos.values()
        for item in items
    )

    return {
        "extra_hoy": extra_hoy,
        "restock_inminente": restock_inminente,
        "surtir_minimo": surtir_minimo,
        "tragos_sueltos": tragos_sueltos,
        "total_botellas_extra": total_botellas_extra,
        "total_tragos_sueltos": total_tragos_sueltos,
    }
