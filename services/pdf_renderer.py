# ============================================================
# services/pdf_renderer.py - PDF de orden de surtido
# ------------------------------------------------------------
# Genera el PDF que el gerente firma cada manana antes del surtido.
# Estructura (Seccion 6 del M3):
#   1. Encabezado con fecha, modo de operacion, modelo activo
#   2. Tabla de SKUs ordenados por categoria + bottles + refuerzo
#   3. Alertas contextuales del LLM (si aplica)
#   4. Resumen de guardrails activados
#   5. Linea de firma del gerente
# ============================================================

from datetime import date
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


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
) -> None:
    """Renderiza el PDF de orden de surtido y lo escribe en output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        topMargin=2 * cm, bottomMargin=2 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle(
        "titulo", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#1a3a5c"),
        alignment=1, spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"], fontSize=12,
        textColor=colors.HexColor("#1a3a5c"), spaceAfter=6,
    )
    body = styles["BodyText"]

    story = []

    # Encabezado
    story.append(Paragraph("Cantina La Castellana - Orden de Surtido Diaria", titulo_style))
    story.append(Paragraph(
        f"<b>Fecha objetivo:</b> {prediction_date.isoformat()}<br/>"
        f"<b>Modo de operacion:</b> {operation_mode.upper()}<br/>"
        f"<b>Modelo:</b> {model_version}",
        body,
    ))
    if fallback_used:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"<b><font color='red'>AVISO: FALLBACK ACTIVADO.</font></b> "
            f"Razon: {fallback_reason}. La orden se genero con baseline PM 4 semanas.",
            body,
        ))
    story.append(Spacer(1, 12))

    # Resumen de guardrails
    story.append(Paragraph("Resumen de guardrails aplicados:", h2))
    if any(v > 0 for v in guardrails_summary.values()):
        items = ", ".join(f"{k}: {v} SKUs" for k, v in guardrails_summary.items() if v > 0)
        story.append(Paragraph(items, body))
    else:
        story.append(Paragraph("Ninguno (prediccion cruda sin modificaciones).", body))
    story.append(Spacer(1, 12))

    # Alertas contextuales (si las hay)
    if alertas_contextuales:
        story.append(Paragraph("Alertas contextuales (LLM):", h2))
        for linea in alertas_contextuales.split("\n"):
            story.append(Paragraph(linea, body))
        story.append(Spacer(1, 12))

    # Tabla de predicciones (ordenadas por categoria, luego por bottles desc)
    story.append(Paragraph("Surtido sugerido (10:00 AM):", h2))
    preds_para_pdf = sorted(
        predictions,
        key=lambda p: (p.get("categoria") or "ZZZ", -p["bottles"]),
    )
    headers = ["SKU", "Categoria", "Unid.", "Botellas", "GRs", "Refuerzo PM"]
    rows = [headers]
    for p in preds_para_pdf:
        if p["pred_final"] <= 0 and p["bottles"] == 0:
            continue  # No bajar nada de este SKU
        gr_str = ",".join(p["guardrails_applied"]) if p["guardrails_applied"] else "-"
        ref = "Si" if p["refuerzo_vespertino"] else "-"
        rows.append([
            p["sku"][:30], p.get("categoria") or "-",
            str(int(p["pred_final"])), str(p["bottles"]), gr_str, ref,
        ])

    if len(rows) > 1:
        t = Table(rows, repeatRows=1, colWidths=[5.5 * cm, 3 * cm, 1.5 * cm, 1.8 * cm, 1.8 * cm, 1.6 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3a5c")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("Sin SKUs con prediccion positiva (todos en surtido minimo).", body))

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "<b>Firma del gerente:</b> _________________________________  "
        "<b>Fecha/hora:</b> _________________________________",
        body,
    ))
    if operation_mode == "shadow":
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            "<i>MODO SHADOW: Esta orden se archiva con timestamp. NO se entrega "
            "al corredor. La operacion sigue su flujo normal sin sistema.</i>",
            body,
        ))

    doc.build(story)
