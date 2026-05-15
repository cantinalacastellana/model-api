# ============================================================
# services/pdf_renderer.py - PDF de orden de surtido
# ------------------------------------------------------------
# Replica EL FORMATO del artefacto M3 de Jose Emilio:
#
#   [Encabezado]
#       CANTINA LA CASTELLANA
#       <DIA SEMANA> <FECHA>
#       [Badges contextuales: QUINCENA, FIN DE SEMANA, FESTIVO]
#
#   [Instruccion operativa]
#       Hora de reabastecimiento: 10:00 AM ...
#
#   [Bloque "EXTRA HOY"]
#       Licores que requieren botellas adicionales
#
#   [Bloque "TRAGOS SUELTOS"]
#       Cocteles, cervezas, refrescos en unidades
#
#   [Bloque "RECORDATORIO STOCK MINIMO"]
#       Restock inminente + surtido minimo por categoria
#
#   [Pie: modelo + WAPE + timestamp]
#   [Doble firma: Gerente + Corredor]
#
# El supuesto operativo es que cada SKU YA TIENE 1 botella en
# barra; las cantidades son SIEMPRE adicionales a esa base.
# ============================================================

from __future__ import annotations
from datetime import date, datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable,
)

from services.clasificacion_orden import (
    clasificar_para_orden, UMBRAL_RESTOCK_INMINENTE,
)


# ============================================================
# Constantes de estilo - paleta sobria de cantina historica
# ============================================================
COLOR_NEGRO = colors.HexColor("#1a1a1a")
COLOR_DORADO = colors.HexColor("#b8862a")
COLOR_DORADO_OSCURO = colors.HexColor("#7a571c")
COLOR_VINO = colors.HexColor("#7a2828")
COLOR_GRIS = colors.HexColor("#666666")
COLOR_FONDO_ENCABEZADO = colors.HexColor("#f5efe0")  # crema papel
COLOR_FONDO_FILA = colors.HexColor("#faf6ec")


DIAS_ES = {
    0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
    4: "Viernes", 5: "Sábado", 6: "Domingo",
}
MESES_ES = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}


# ============================================================
# Funcion principal
# ============================================================
def generar_pdf_orden(
    output_path: Path,
    prediction_date: date,
    model_version: str,
    operation_mode: str,
    predictions: list[dict],
    guardrails_summary: dict[str, int],
    fallback_used: bool,
    fallback_reason: str | None,
    alertas_contextuales: str | None,
    wape_val: float | None = None,
    wape_simple_val: float | None = None,
) -> None:
    """Renderiza el PDF en formato artefacto M3."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        topMargin=1.5 * cm, bottomMargin=2 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title=f"Orden La Castellana {prediction_date.isoformat()}",
        author="Sistema La Castellana",
    )

    styles = _build_styles()
    story = []

    # 1. ENCABEZADO
    story += _seccion_encabezado(prediction_date, styles)

    # 2. AVISO DE FALLBACK (si aplica)
    if fallback_used:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"<b>AVISO: FALLBACK ACTIVADO.</b> "
            f"Razón: {fallback_reason}. La orden se generó con baseline PM 4 semanas.",
            styles["aviso"],
        ))
        story.append(Spacer(1, 8))

    # 3. INSTRUCCION OPERATIVA
    story.append(Paragraph(
        "<b>Hora de reabastecimiento: 10:00 AM</b> &mdash; "
        "Bajar todo el producto del almacén en la mañana. "
        "Reponer conforme se agote durante el día.",
        styles["instruccion"],
    ))
    story.append(Spacer(1, 10))

    # 4. ALERTAS CONTEXTUALES (LLM)
    if alertas_contextuales and alertas_contextuales.strip():
        story += _seccion_alertas_contextuales(alertas_contextuales, styles)

    # 5. CLASIFICACION DE LA ORDEN
    clasif = clasificar_para_orden(predictions)

    # 5a. EXTRA HOY (licores)
    if clasif["extra_hoy"]:
        story += _seccion_extra_hoy(clasif, styles)
    else:
        story.append(Paragraph(
            "<i>Hoy no se requieren botellas adicionales de licor. "
            "La barra opera con la base de 1 botella por SKU.</i>",
            styles["body"],
        ))
        story.append(Spacer(1, 10))

    # 5b. TRAGOS SUELTOS
    if clasif["tragos_sueltos"]:
        story += _seccion_tragos_sueltos(clasif, styles)

    # 5c. RECORDATORIO + RESTOCK INMINENTE + SURTIDO MINIMO
    story += _seccion_stock_minimo(clasif, styles)

    # 6. PIE TECNICO
    story += _seccion_pie_tecnico(
        model_version, wape_val, wape_simple_val,
        guardrails_summary, fallback_used, styles,
    )

    # 7. FIRMAS
    story += _seccion_firmas(operation_mode, styles)

    doc.build(story)


# ============================================================
# Estilos
# ============================================================
def _build_styles():
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"],
            fontName="Helvetica-Bold", fontSize=20,
            textColor=COLOR_NEGRO, alignment=TA_CENTER,
            spaceAfter=2, leading=22,
        ),
        "fecha": ParagraphStyle(
            "fecha", parent=base["Heading2"],
            fontName="Helvetica", fontSize=14,
            textColor=COLOR_DORADO_OSCURO, alignment=TA_CENTER,
            spaceAfter=4, leading=16,
        ),
        "badges": ParagraphStyle(
            "badges", parent=base["BodyText"],
            fontName="Helvetica-Bold", fontSize=10,
            textColor=COLOR_DORADO, alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=12,
            textColor=COLOR_NEGRO, spaceBefore=10, spaceAfter=6,
        ),
        "h2_vino": ParagraphStyle(
            "h2_vino", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=12,
            textColor=COLOR_VINO, spaceBefore=10, spaceAfter=6,
        ),
        "h3_categoria": ParagraphStyle(
            "h3_categoria", parent=base["BodyText"],
            fontName="Helvetica-Bold", fontSize=10,
            textColor=COLOR_DORADO_OSCURO, spaceBefore=4, spaceAfter=3,
            leftIndent=4,
        ),
        "instruccion": ParagraphStyle(
            "instruccion", parent=base["BodyText"],
            fontName="Helvetica", fontSize=9,
            textColor=COLOR_NEGRO, leftIndent=4,
            borderColor=COLOR_DORADO, borderWidth=0,
            borderPadding=4,
            backColor=COLOR_FONDO_ENCABEZADO,
            spaceAfter=4,
        ),
        "aviso": ParagraphStyle(
            "aviso", parent=base["BodyText"],
            fontName="Helvetica-Bold", fontSize=10,
            textColor=COLOR_VINO,
            backColor=colors.HexColor("#fbe9e9"),
            borderColor=COLOR_VINO, borderWidth=0.5,
            borderPadding=6, leading=12,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontName="Helvetica", fontSize=9.5,
            textColor=COLOR_NEGRO, leading=12,
        ),
        "body_muted": ParagraphStyle(
            "body_muted", parent=base["BodyText"],
            fontName="Helvetica", fontSize=8.5,
            textColor=COLOR_GRIS, leading=11, leftIndent=4,
        ),
        "body_italic": ParagraphStyle(
            "body_italic", parent=base["BodyText"],
            fontName="Helvetica-Oblique", fontSize=9,
            textColor=COLOR_NEGRO, leading=11,
        ),
        "pie": ParagraphStyle(
            "pie", parent=base["BodyText"],
            fontName="Helvetica", fontSize=7.5,
            textColor=COLOR_GRIS, alignment=TA_CENTER,
        ),
        "firma_label": ParagraphStyle(
            "firma_label", parent=base["BodyText"],
            fontName="Helvetica-Bold", fontSize=9,
            textColor=COLOR_NEGRO, alignment=TA_CENTER,
            spaceBefore=0, spaceAfter=2,
        ),
        "firma_sub": ParagraphStyle(
            "firma_sub", parent=base["BodyText"],
            fontName="Helvetica-Oblique", fontSize=7.5,
            textColor=COLOR_GRIS, alignment=TA_CENTER,
        ),
    }


# ============================================================
# Secciones
# ============================================================
def _seccion_encabezado(fecha: date, styles) -> list:
    """Cantina + dia + fecha + badges contextuales."""
    story = []
    story.append(HRFlowable(width="100%", thickness=2, color=COLOR_DORADO, spaceBefore=0, spaceAfter=6))
    story.append(Paragraph("CANTINA LA CASTELLANA", styles["h1"]))

    dia_str = DIAS_ES[fecha.weekday()].upper()
    fecha_fmt = f"{fecha.day:02d}/{MESES_ES[fecha.month].upper()}/{fecha.year}"
    story.append(Paragraph(f"{dia_str} {fecha_fmt}", styles["fecha"]))

    badges = []
    if fecha.day in (14, 15, 30, 31):
        badges.append("[ QUINCENA ]")
    if fecha.weekday() >= 4:
        badges.append("[ FIN DE SEMANA ]")
    festividades = _detectar_festividad(fecha)
    for f in festividades:
        badges.append(f"[ {f.upper()} ]")

    if badges:
        story.append(Paragraph("  ".join(badges), styles["badges"]))

    story.append(HRFlowable(width="100%", thickness=0.6, color=COLOR_DORADO_OSCURO, spaceBefore=2, spaceAfter=8))
    return story


def _detectar_festividad(fecha: date) -> list[str]:
    """Festividades fijas del calendario mexicano (badges en encabezado)."""
    m, d = fecha.month, fecha.day
    festividades = []
    if (m, d) == (1, 1):  festividades.append("Año Nuevo")
    if (m, d) == (1, 6):  festividades.append("Día de Reyes")
    if (m, d) == (2, 5):  festividades.append("Día Constitución")
    if (m, d) == (2, 14): festividades.append("San Valentín")
    if (m, d) == (3, 21): festividades.append("Natalicio Juárez")
    if (m, d) == (4, 30): festividades.append("Día del Niño")
    if (m, d) == (5, 1):  festividades.append("Día del Trabajo")
    if (m, d) == (5, 5):  festividades.append("Batalla de Puebla")
    if (m, d) == (5, 10): festividades.append("Día de la Madre")
    if (m, d) == (9, 15): festividades.append("Grito de Independencia")
    if (m, d) == (9, 16): festividades.append("Día Independencia")
    if (m, d) == (11, 1): festividades.append("Día Todos Santos")
    if (m, d) == (11, 2): festividades.append("Día de Muertos")
    if (m, d) == (11, 20): festividades.append("Revolución Mexicana")
    if (m, d) == (12, 12): festividades.append("Virgen Guadalupe")
    if (m, d) in [(12, 24), (12, 25)]: festividades.append("Navidad")
    if (m, d) == (12, 31): festividades.append("Fin de Año")
    return festividades


def _seccion_alertas_contextuales(texto: str, styles) -> list:
    story = []
    story.append(Paragraph("CONTEXTO DEL DÍA", styles["h2"]))
    for linea in texto.split("\n"):
        if linea.strip():
            story.append(Paragraph(linea.replace("&", "&amp;"), styles["body"]))
    story.append(Spacer(1, 8))
    return story


def _seccion_extra_hoy(clasif: dict, styles) -> list:
    story = []
    total_botellas = clasif["total_botellas_extra"]
    total_tragos = clasif["total_tragos_sueltos"]
    header_text = (
        f"PRODUCTOS QUE REQUIEREN CANTIDAD EXTRA HOY "
        f"({total_botellas} botella{'s' if total_botellas != 1 else ''} adicional"
        f"{'es' if total_botellas != 1 else ''}"
    )
    if total_tragos > 0:
        header_text += f" + {total_tragos} tragos sueltos)"
    else:
        header_text += ")"
    story.append(Paragraph(header_text, styles["h2_vino"]))

    por_categoria: dict[str, list[dict]] = {}
    for item in clasif["extra_hoy"]:
        por_categoria.setdefault(item["categoria"], []).append(item)

    for categoria, items in por_categoria.items():
        story.append(Paragraph(f"&#9830; {categoria}", styles["h3_categoria"]))
        rows = [["Producto", "Cantidad", "Detalle"]]
        for it in items:
            cant_str = f"{it['botellas_extra']} botella{'s' if it['botellas_extra'] != 1 else ''}"
            rows.append([it["sku_base"], cant_str, it["detalle"]])
        t = Table(rows, repeatRows=1, colWidths=[6 * cm, 3 * cm, 8.5 * cm])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_NEGRO),
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_FONDO_ENCABEZADO),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, COLOR_DORADO_OSCURO),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_FONDO_FILA]),
            ("LINEBELOW", (0, -1), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ]))
        story.append(t)
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 6))
    return story


def _seccion_tragos_sueltos(clasif: dict, styles) -> list:
    story = []
    story.append(HRFlowable(width="60%", thickness=0.4, color=COLOR_DORADO, spaceBefore=4, spaceAfter=4, hAlign="CENTER"))
    story.append(Paragraph("TRAGOS SUELTOS (sin botella propia)", styles["h2"]))

    for categoria, items in clasif["tragos_sueltos"].items():
        story.append(Paragraph(f"&#9830; {categoria}", styles["h3_categoria"]))
        rows = [["Producto", "Unidades"]]
        for it in items:
            rows.append([it["sku_base"], f"{it['unidades']} unid."])
        t = Table(rows, repeatRows=1, colWidths=[11 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_FONDO_ENCABEZADO),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, COLOR_DORADO_OSCURO),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_FONDO_FILA]),
        ]))
        story.append(t)
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 6))
    return story


def _seccion_stock_minimo(clasif: dict, styles) -> list:
    story = []
    story.append(HRFlowable(width="100%", thickness=0.6, color=COLOR_DORADO_OSCURO, spaceBefore=8, spaceAfter=6))
    story.append(Paragraph("RECORDATORIO STOCK MÍNIMO (1 botella por SKU)", styles["h2"]))
    story.append(Paragraph(
        "Estos productos se predicen en cantidad baja. Verificar que haya al menos "
        "1 botella en barra antes de abrir.",
        styles["body_italic"],
    ))
    story.append(Spacer(1, 6))

    if clasif["restock_inminente"]:
        story.append(Paragraph(
            f"<b>Posible restock inminente</b> &mdash; demanda predice consumo "
            f"≥{int(UMBRAL_RESTOCK_INMINENTE*100)}% de 1 botella:",
            styles["body"],
        ))
        for it in clasif["restock_inminente"]:
            sku = it["sku"]
            n = it["pred_final"]
            oz = it["oz_consumidas"]
            pct = int(it["pct_botella"] * 100)
            story.append(Paragraph(
                f"&bull; <b>{sku}</b> &mdash; {n} sencillo → {oz} oz "
                f"<font color='{COLOR_VINO.hexval()}'>({pct}% botella)</font>",
                styles["body_muted"],
            ))
        story.append(Spacer(1, 6))

    if clasif["surtir_minimo"]:
        story.append(Paragraph(
            "<b>Surtir 1 de cada uno</b> (demanda baja, sin alerta):",
            styles["body"],
        ))
        for categoria in sorted(clasif["surtir_minimo"].keys()):
            skus = clasif["surtir_minimo"][categoria]
            linea = f"<b>{categoria}:</b> {', '.join(skus)}"
            story.append(Paragraph(linea, styles["body_muted"]))
        story.append(Spacer(1, 6))

    return story


def _seccion_pie_tecnico(
    model_version, wape_val, wape_simple_val,
    guardrails_summary, fallback_used, styles,
) -> list:
    story = []
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.4, color=COLOR_GRIS, spaceBefore=0, spaceAfter=4))

    partes = [f"Modelo: <b>{model_version}</b>"]
    if wape_val is not None:
        partes.append(f"WAPE Ponderado VAL: {wape_val:.2f}%")
    if wape_simple_val is not None:
        partes.append(f"WAPE Simple VAL: {wape_simple_val:.2f}%")

    gr_activos = [f"{k}: {v}" for k, v in guardrails_summary.items() if v > 0]
    if gr_activos:
        partes.append("Guardrails: " + ", ".join(gr_activos))

    if fallback_used:
        partes.append("<font color='{}'><b>FALLBACK ACTIVO</b></font>".format(COLOR_VINO.hexval()))

    partes.append(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    story.append(Paragraph(" | ".join(partes), styles["pie"]))
    return story


def _seccion_firmas(operation_mode, styles) -> list:
    story = []
    story.append(Spacer(1, 22))
    rows = [
        [Paragraph("_________________________________", styles["firma_label"]),
         Paragraph("_________________________________", styles["firma_label"])],
        [Paragraph("<b>Firma Gerente</b>", styles["firma_label"]),
         Paragraph("<b>Firma Corredor</b>", styles["firma_label"])],
        [Paragraph("Valida cantidades y autoriza orden", styles["firma_sub"]),
         Paragraph("Confirma entrega al almacén", styles["firma_sub"])],
    ]
    t = Table(rows, colWidths=[8.5 * cm, 8.5 * cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(t)

    if operation_mode == "shadow":
        story.append(Spacer(1, 14))
        story.append(Paragraph(
            "<i>MODO SHADOW: Esta orden se archiva con timestamp. NO se entrega "
            "al corredor. La operación sigue su flujo normal sin sistema.</i>",
            styles["body_italic"],
        ))

    return story
