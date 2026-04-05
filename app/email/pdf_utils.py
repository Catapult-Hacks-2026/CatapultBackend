from __future__ import annotations

from io import BytesIO

def render_text_pdf(title: str, lines: list[str]) -> bytes:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError:
        return _render_fallback_pdf(title, lines)

    def _wrap_line(text: str, max_width: float, font_name: str, font_size: int) -> list[str]:
        if not text:
            return [""]

        words = text.split()
        wrapped_lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if stringWidth(candidate, font_name, font_size) <= max_width:
                current = candidate
                continue
            if current:
                wrapped_lines.append(current)
            current = word
        if current:
            wrapped_lines.append(current)
        return wrapped_lines or [""]

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
    pdf.setAuthor("catapult_main")

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


def _escape_pdf_text(text: str) -> str:
    safe = text.encode("latin-1", "replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_fallback_pdf(title: str, lines: list[str]) -> bytes:
    page_width = 612
    page_height = 792
    start_x = 54
    start_y = 740
    line_gap = 16
    max_lines_per_page = 42
    all_lines = [title, ""] + list(lines)
    pages = [
        all_lines[index:index + max_lines_per_page]
        for index in range(0, max(len(all_lines), 1), max_lines_per_page)
    ]
    objects: list[bytes] = []

    page_object_numbers: list[int] = []
    content_object_numbers: list[int] = []
    next_object_number = 3
    for _ in pages:
        page_object_numbers.append(next_object_number)
        content_object_numbers.append(next_object_number + 1)
        next_object_number += 2
    font_object_number = next_object_number

    objects.append(
        b"<< /Type /Catalog /Pages 2 0 R >>"
    )
    kids = " ".join(f"{object_number} 0 R" for object_number in page_object_numbers)
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_numbers)} >>".encode("latin-1")
    )

    for page_index, page_lines in enumerate(pages):
        content_stream_lines = ["BT", "/F1 12 Tf"]
        y = start_y
        for line in page_lines:
            content_stream_lines.append(f"1 0 0 1 {start_x} {y} Tm ({_escape_pdf_text(line)}) Tj")
            y -= line_gap
        content_stream_lines.append("ET")
        content_stream = "\n".join(content_stream_lines).encode("latin-1")
        content_object = (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
            + content_stream
            + b"\nendstream"
        )
        page_object = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
            f"/Contents {content_object_numbers[page_index]} 0 R >>"
        ).encode("latin-1")
        objects.append(page_object)
        objects.append(content_object)

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode("latin-1"))
        buffer.write(obj)
        buffer.write(b"\nendobj\n")
    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    buffer.write(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )
    return buffer.getvalue()
