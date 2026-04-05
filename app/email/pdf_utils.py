from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def _wrap_line(text: str, max_width: float, font_name: str, font_size: int) -> list[str]:
    if not text:
        return [""]

    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def render_text_pdf(title: str, lines: list[str]) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, pdfVersion=(1, 4))
    page_width, page_height = letter

    margin_x = 54
    top_margin = 72
    bottom_margin = 54
    line_gap = 16
    body_font = "Helvetica"
    body_size = 11
    title_font = "Helvetica-Bold"
    title_size = 15
    max_width = page_width - (2 * margin_x)

    y = page_height - top_margin

    def new_page() -> float:
        pdf.showPage()
        return page_height - top_margin

    pdf.setTitle(title)
    pdf.setAuthor("CatapultBackend")

    pdf.setFont(title_font, title_size)
    for wrapped in _wrap_line(title, max_width, title_font, title_size):
        if y < bottom_margin:
            y = new_page()
            pdf.setFont(title_font, title_size)
        pdf.drawString(margin_x, y, wrapped)
        y -= line_gap

    y -= 4
    pdf.setFont(body_font, body_size)
    for raw_line in lines:
        wrapped_lines = _wrap_line(raw_line, max_width, body_font, body_size)
        for wrapped in wrapped_lines:
            if y < bottom_margin:
                y = new_page()
                pdf.setFont(body_font, body_size)
            pdf.drawString(margin_x, y, wrapped)
            y -= line_gap

    pdf.save()
    return buffer.getvalue()
