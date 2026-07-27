from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import platform_db


def render_invoice(invoice_id: int) -> tuple[Path, str]:
    with platform_db.connect() as conn:
        invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not invoice:
            raise ValueError("Invoice not found")
        if invoice["status"] == "Draft" or not invoice["number"]:
            raise ValueError("Only an issued invoice can be rendered")
        lines = conn.execute("SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY sort_order, id", (invoice_id,)).fetchall()
        profile = conn.execute("SELECT * FROM business_profile WHERE id = 1").fetchone()
    directory = platform_db.documents_root() / "invoices"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{invoice['number']}.pdf"
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Right", parent=styles["Normal"], alignment=TA_RIGHT))
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    story = [
        Table(
            [[Paragraph(f"<b>{_escape((profile['trading_name'] or profile['legal_name']) if profile else 'CRM Workspace')}</b>", styles["Title"]), Paragraph("<b>INVOICE</b>", styles["Right"])],
             [Paragraph(_business_details(profile), styles["Normal"]), Paragraph(f"<b>{_escape(invoice['number'])}</b><br/>Issued: {_escape(invoice['issued_on'] or '')}<br/>Due: {_escape(invoice['due_on'])}", styles["Right"])]],
            colWidths=[105 * mm, 55 * mm],
        ),
        Spacer(1, 10 * mm),
        Paragraph("<b>Bill to</b>", styles["Heading3"]),
        Paragraph(f"{_escape(invoice['customer_name'])}<br/>{_escape(invoice['customer_address']).replace(chr(10), '<br/>')}", styles["Normal"]),
        Spacer(1, 8 * mm),
    ]
    table_data = [["Description", "Qty", "Unit", "VAT", "Total"]]
    for line in lines:
        table_data.append([
            line["description"],
            line["quantity"],
            _money(line["unit_price_pence"], invoice["currency"]),
            f"{Decimal(line['tax_rate_bps']) / Decimal(100):.2f}%",
            _money(line["total_pence"], invoice["currency"]),
        ])
    table = Table(table_data, colWidths=[76 * mm, 18 * mm, 24 * mm, 18 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#101827")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fb")]),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d8e1ec")),
    ]))
    story.extend([table, Spacer(1, 8 * mm)])
    totals = Table([
        ["Net", _money(invoice["net_pence"], invoice["currency"])],
        ["VAT", _money(invoice["vat_pence"], invoice["currency"])],
        [Paragraph("<b>Total</b>", styles["Normal"]), Paragraph(f"<b>{_money(invoice['total_pence'], invoice['currency'])}</b>", styles["Right"])],
    ], colWidths=[35 * mm, 35 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#101827")), ("TOPPADDING", (0, -1), (-1, -1), 6)]))
    story.extend([totals, Spacer(1, 8 * mm)])
    if invoice["notes"]:
        story.extend([Paragraph("<b>Notes</b>", styles["Heading3"]), Paragraph(_escape(invoice["notes"]).replace(chr(10), "<br/>"), styles["Normal"]), Spacer(1, 6 * mm)])
    if profile and profile["bank_details"]:
        story.extend([Paragraph("<b>Payment details</b>", styles["Heading3"]), Paragraph(_escape(profile["bank_details"]).replace(chr(10), "<br/>"), styles["Normal"])])
    doc.build(story)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with platform_db.connect() as conn:
        conn.execute("UPDATE invoices SET pdf_path = ?, pdf_sha256 = ? WHERE id = ?", (str(path), digest, invoice_id))
    return path, digest


def _money(pence: int, currency: str) -> str:
    symbol = "£" if currency.upper() == "GBP" else f"{currency.upper()} "
    return f"{symbol}{Decimal(pence) / Decimal(100):,.2f}"


def _escape(value: str) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _business_details(profile) -> str:
    if not profile:
        return ""
    parts = [profile["legal_name"], profile["company_number"], f"VAT: {profile['vat_number']}" if profile["vat_number"] else "", profile["invoice_email"], profile["invoice_phone"]]
    return "<br/>".join(_escape(part) for part in parts if part)

