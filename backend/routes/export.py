import io
import re
from datetime import datetime
import asyncio
from typing import Annotated, Optional

from credits_wallet import debit_credits
from database import get_db
from deps import require_wallet_user
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import User
from pricing import billed_mru_to_wallet_units_debit, export_job_billed
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

router = APIRouter()


class ExportRequest(BaseModel):
    lesson: str
    subject: str = "Lecture"
    filename: str = "lesson"


def strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"#{1,6}\s+", "", text)
    return text.strip()


@router.post("/export/pdf")
async def export_pdf(
    req: ExportRequest,
    db: Annotated[Session, Depends(get_db)],
    _u: Annotated[Optional[User], Depends(require_wallet_user)],
):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2 * cm,
        title=req.subject,
    )

    styles = getSampleStyleSheet()
    primary = colors.HexColor("#1a1a2e")
    accent = colors.HexColor("#4f46e5")
    light = colors.HexColor("#f0f0ff")
    muted = colors.HexColor("#6b7280")

    title_style = ParagraphStyle(
        "LTitle",
        fontSize=26,
        textColor=primary,
        spaceAfter=6,
        leading=32,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "LSub",
        fontSize=12,
        textColor=muted,
        spaceAfter=4,
        alignment=TA_CENTER,
    )
    h2_style = ParagraphStyle(
        "LH2",
        fontSize=15,
        textColor=accent,
        spaceBefore=18,
        spaceAfter=8,
        fontName="Helvetica-Bold",
    )
    h3_style = ParagraphStyle(
        "LH3",
        fontSize=12,
        textColor=primary,
        spaceBefore=12,
        spaceAfter=6,
        fontName="Helvetica-Bold",
    )
    body_style = ParagraphStyle(
        "LBody",
        fontSize=10,
        textColor=primary,
        leading=16,
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    bold_style = ParagraphStyle(
        "LBold",
        fontSize=10,
        textColor=primary,
        leading=16,
        spaceAfter=4,
        fontName="Helvetica-Bold",
    )
    bullet_style = ParagraphStyle(
        "LBullet",
        fontSize=10,
        textColor=primary,
        leading=16,
        spaceAfter=4,
        leftIndent=16,
        bulletIndent=4,
    )

    story: list = []

    story.append(Spacer(1, 3 * cm))
    story.append(HRFlowable(width="100%", thickness=3, color=accent))
    story.append(Spacer(1, 0.5 * cm))
    story.append(
        Paragraph(
            "LecturAI",
            ParagraphStyle(
                "Brand",
                fontSize=11,
                textColor=accent,
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
            ),
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    title_match = re.search(r"##\s*1\.\s*TITLE\s*\n\s*(.+)", req.lesson, re.MULTILINE)
    lesson_title = title_match.group(1).strip() if title_match else req.subject

    story.append(Paragraph(lesson_title.replace("&", "&amp;"), title_style))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"Subject: {req.subject}", subtitle_style))
    story.append(
        Paragraph(
            f"Generated: {datetime.now().strftime('%B %d, %Y')}",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=1, color=light))
    story.append(PageBreak())

    lines = req.lesson.split("\n")
    i = 0
    in_table = False
    table_rows: list[list[str]] = []

    while i < len(lines):
        line = lines[i]

        if line.startswith("## "):
            if in_table and table_rows:
                _flush_table_pdf(story, table_rows, accent, light)
                in_table = False
                table_rows = []
            story.append(Paragraph(strip_markdown(line[3:]), h2_style))

        elif line.startswith("### "):
            if in_table and table_rows:
                _flush_table_pdf(story, table_rows, accent, light)
                in_table = False
                table_rows = []
            story.append(Paragraph(strip_markdown(line[4:]), h3_style))

        elif line.startswith("| ") and "|" in line:
            if not in_table:
                in_table = True
                table_rows = []
            if "---" not in line:
                cells = [strip_markdown(c.strip()) for c in line.split("|") if c.strip()]
                table_rows.append(cells)
        else:
            if in_table and table_rows:
                _flush_table_pdf(story, table_rows, accent, light)
                in_table = False
                table_rows = []

            if line.startswith("- ") or line.startswith("* "):
                text = strip_markdown(line[2:])
                story.append(Paragraph(f"• {text}", bullet_style))
            elif re.match(r"^\*\*(.+?)\*\*\s*[—\-]", line):
                story.append(Paragraph(strip_markdown(line), body_style))
            elif line.strip() == "---":
                story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e0e0e0")))
                story.append(Spacer(1, 0.2 * cm))
            elif line.strip():
                story.append(Paragraph(strip_markdown(line), bold_style if line.startswith("**") else body_style))
            else:
                story.append(Spacer(1, 0.15 * cm))

        i += 1

    if in_table and table_rows:
        _flush_table_pdf(story, table_rows, accent, light)

    await asyncio.to_thread(doc.build, story)
    buffer.seek(0)

    _, billed_mru = export_job_billed()
    charged = billed_mru_to_wallet_units_debit(billed_mru)
    if charged > 0:
        debit_credits(db, _u, charged)

    safe_name = re.sub(r"[^\w\-]", "_", req.filename)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_lesson.pdf"'},
    )


def _flush_table_pdf(story, table_rows, accent, light):
    if len(table_rows) <= 1:
        return
    col_count = max(len(r) for r in table_rows)
    padded = [r + [""] * (col_count - len(r)) for r in table_rows]
    col_width = (A4[0] - 4 * cm) / col_count
    t = Table(padded, colWidths=[col_width] * col_count)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), accent),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 0.3 * cm))


@router.post("/export/docx")
async def export_docx(
    req: ExportRequest,
    db: Annotated[Session, Depends(get_db)],
    _u: Annotated[Optional[User], Depends(require_wallet_user)],
):
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    cover = doc.add_paragraph()
    cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = cover.add_run("LecturAI")
    run.font.color.rgb = RGBColor(79, 70, 229)
    run.font.size = Pt(11)
    run.font.bold = True

    title_match = re.search(r"##\s*1\.\s*TITLE\s*\n\s*(.+)", req.lesson, re.MULTILINE)
    lesson_title = title_match.group(1).strip() if title_match else req.subject

    h = doc.add_heading(lesson_title, 0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = doc.add_paragraph(
        f"Subject: {req.subject}  |  {datetime.now().strftime('%B %d, %Y')}"
    )
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.runs[0].font.color.rgb = RGBColor(107, 114, 128)

    doc.add_page_break()

    table_buffer: list[list[str]] = []
    collecting_table = False

    def flush_table():
        nonlocal collecting_table, table_buffer
        if not table_buffer:
            collecting_table = False
            table_buffer = []
            return
        rows = len(table_buffer)
        cols = max(len(r) for r in table_buffer)
        table = doc.add_table(rows=rows, cols=cols)
        table.style = "Table Grid"
        for ri, row in enumerate(table_buffer):
            for ci in range(cols):
                cell_text = row[ci] if ci < len(row) else ""
                table.rows[ri].cells[ci].text = cell_text
        doc.add_paragraph("")
        collecting_table = False
        table_buffer = []

    for line in req.lesson.split("\n"):
        if line.startswith("## "):
            flush_table()
            doc.add_heading(line[3:].strip(), level=1)
        elif line.startswith("### "):
            flush_table()
            doc.add_heading(line[4:].strip(), level=2)
        elif line.startswith("| ") and "|" in line:
            if "---" in line:
                continue
            cells = [strip_markdown(c.strip()) for c in line.split("|") if c.strip()]
            if cells:
                collecting_table = True
                table_buffer.append(cells)
        elif line.startswith("- ") or line.startswith("* "):
            flush_table()
            doc.add_paragraph(strip_markdown(line[2:]), style="List Bullet")
        elif line.strip() == "---":
            flush_table()
            doc.add_paragraph("─" * 60)
        elif line.strip():
            flush_table()
            doc.add_paragraph(strip_markdown(line))
        else:
            flush_table()

    flush_table()

    buffer = io.BytesIO()
    await asyncio.to_thread(doc.save, buffer)
    buffer.seek(0)

    _, billed_mru_d = export_job_billed()
    charged_d = billed_mru_to_wallet_units_debit(billed_mru_d)
    if charged_d > 0:
        debit_credits(db, _u, charged_d)

    safe_name = re.sub(r"[^\w\-]", "_", req.filename)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_lesson.docx"'},
    )
