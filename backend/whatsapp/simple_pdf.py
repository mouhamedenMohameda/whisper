"""Génération PDF simplifiée pour le bot WhatsApp — version légère de `routes/export.py`.

Pourquoi un module séparé : `routes/export.py` mélange logique HTTP (FastAPI route handler,
auth, debit wallet, headers de réponse) et logique de rendu PDF. Pour appeler depuis un worker
background non-HTTP, on a besoin d'une fonction pure `markdown → bytes` sans dépendance route.

Ce module produit un PDF "lisible et propre" (pas une œuvre d'art) qui suffit largement pour
qu'un étudiant lise sa fiche dans WhatsApp.

Support arabe :
  - Police TTF avec glyphs arabes (recherche WHATSAPP_ARABIC_FONT_PATH / chemins système courants).
  - ``arabic-reshaper`` : transforme chaque lettre dans sa forme contextuelle (initiale/médiale/finale).
  - ``python-bidi`` : applique l'algorithme bidirectionnel Unicode (RTL).
  - Si une des briques manque, le PDF arabe sera dégradé mais ne plantera pas (carrés visibles).
"""

from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

logger = logging.getLogger(__name__)


# === Police arabe : enregistrée 1× au module load, idempotent. ============================
_ARABIC_FONT_NAME = "LecturAIArabic"
_ARABIC_FONT_BOLD = "LecturAIArabicBold"
_ARABIC_REGISTERED = False
_ARABIC_BOLD_REGISTERED = False


def _find_first_existing(paths: list[str]) -> Optional[str]:
    for p in paths:
        if p and Path(p).is_file():
            return p
    return None


def _register_arabic_fonts() -> bool:
    """Enregistre une police TTF arabe pour ReportLab si trouvée. Retourne True si OK."""
    global _ARABIC_REGISTERED, _ARABIC_BOLD_REGISTERED
    if _ARABIC_REGISTERED:
        return True

    repo_fonts = Path(__file__).resolve().parent / "fonts"
    regular_candidates = [
        os.getenv("WHATSAPP_ARABIC_FONT_PATH", "").strip(),
        str(repo_fonts / "Amiri-Regular.ttf"),
        str(repo_fonts / "NotoSansArabic-Regular.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/amiri/Amiri-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    bold_candidates = [
        os.getenv("WHATSAPP_ARABIC_FONT_BOLD_PATH", "").strip(),
        str(repo_fonts / "Amiri-Bold.ttf"),
        str(repo_fonts / "NotoSansArabic-Bold.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    regular = _find_first_existing(regular_candidates)
    if regular is None:
        logger.warning(
            "Police arabe introuvable. Installer fonts-noto-core / fonts-dejavu-core "
            "ou poser WHATSAPP_ARABIC_FONT_PATH=/chemin/vers/font.ttf — le PDF arabe "
            "continuera à afficher des carrés."
        )
        return False
    try:
        pdfmetrics.registerFont(TTFont(_ARABIC_FONT_NAME, regular))
        _ARABIC_REGISTERED = True
        logger.info("Police arabe enregistrée : %s", regular)
    except Exception:
        logger.exception("registerFont arabe (regular) échec sur %s", regular)
        return False

    bold = _find_first_existing(bold_candidates)
    if bold:
        try:
            pdfmetrics.registerFont(TTFont(_ARABIC_FONT_BOLD, bold))
            _ARABIC_BOLD_REGISTERED = True
        except Exception:
            logger.exception("registerFont arabe (bold) échec sur %s", bold)
    if not _ARABIC_BOLD_REGISTERED:
        # Fallback : on aliasera _ARABIC_FONT_BOLD vers la regular au moment de l'utilisation.
        logger.info("Pas de variante bold arabe — on utilisera la regular pour les titres.")

    return True


# === Reshape + BiDi ======================================================================
try:
    import arabic_reshaper  # type: ignore
    from bidi.algorithm import get_display  # type: ignore
    _ARABIC_SHAPER_OK = True
except Exception:
    arabic_reshaper = None  # type: ignore
    get_display = None  # type: ignore
    _ARABIC_SHAPER_OK = False
    logger.warning(
        "arabic-reshaper / python-bidi non installés — le texte arabe sera affiché brut "
        "(lettres non liées, ordre incorrect). pip install arabic-reshaper python-bidi"
    )


def _shape_arabic(text: str) -> str:
    if not text or not _ARABIC_SHAPER_OK:
        return text
    try:
        reshaped = arabic_reshaper.reshape(text)  # type: ignore
        return get_display(reshaped)  # type: ignore
    except Exception:
        logger.debug("shape arabic failed", exc_info=True)
        return text


# === Markdown inline → texte ============================================================
def _strip_inline(text: str) -> str:
    """Convertit en HTML inline simple supporté par reportlab Paragraph (gras / italique)."""
    text = re.sub(r"`([^`]+)`", r"<i>\1</i>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


def _strip_inline_plain(text: str) -> str:
    """Variante sans HTML — utilisée pour l'arabe (les balises se mélangent au bidi)."""
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    return text


def _is_rtl(ui_locale: Optional[str]) -> bool:
    return bool(ui_locale) and ui_locale.strip().lower().startswith("ar")


# Sections markdown à *ne pas* rendre dans le PDF de cours (mais conservées dans la base pour
# permettre /quiz, /refaire). On reconnaît par mots-clés dans le titre (insensible à la casse).
_PDF_SKIP_SECTION_KEYWORDS = (
    "quiz", "qcm", "questionnaire",
    "flashcard", "flash card", "carte mémoire", "cartes mémoire", "cartes-mémoire",
    "اختبار", "أسئلة", "بطاقات",
)


def _is_skip_heading(heading_text: str) -> bool:
    h = (heading_text or "").lower()
    return any(k in h for k in _PDF_SKIP_SECTION_KEYWORDS)


def build_lesson_pdf_bytes(
    lesson_markdown: str, subject: str = "General", ui_locale: str = "fr"
) -> tuple[bytes, int]:
    """Renvoie ``(pdf_bytes, page_count)`` — le nb de pages sert à la facturation page-par-page."""
    buf = io.BytesIO()
    rtl = _is_rtl(ui_locale)
    align_body = TA_RIGHT if rtl else TA_LEFT

    # Tente l'enregistrement de la police arabe (idempotent, no-op si déjà fait).
    arabic_font_ok = _register_arabic_fonts() if rtl else False
    body_font = _ARABIC_FONT_NAME if arabic_font_ok else "Helvetica"
    bold_font = (_ARABIC_FONT_BOLD if _ARABIC_BOLD_REGISTERED else _ARABIC_FONT_NAME) if arabic_font_ok else "Helvetica-Bold"

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=subject or "LecturAI",
    )

    base = getSampleStyleSheet()
    brand = colors.HexColor("#ea580c")
    primary = colors.HexColor("#1a1a2e")
    muted = colors.HexColor("#6b7280")

    title_style = ParagraphStyle(
        "Title", parent=base["Title"], fontSize=22, textColor=brand,
        alignment=TA_CENTER, leading=28, spaceAfter=10, fontName=bold_font,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=base["Normal"], fontSize=11, textColor=muted,
        alignment=TA_CENTER, spaceAfter=12, fontName=body_font,
    )
    h1_style = ParagraphStyle(
        "H1", parent=base["Heading1"], fontSize=18, textColor=brand,
        leading=22, spaceBefore=14, spaceAfter=8, alignment=align_body, fontName=bold_font,
    )
    h2_style = ParagraphStyle(
        "H2", parent=base["Heading2"], fontSize=14, textColor=primary,
        leading=18, spaceBefore=12, spaceAfter=6, alignment=align_body, fontName=bold_font,
    )
    h3_style = ParagraphStyle(
        "H3", parent=base["Heading3"], fontSize=12, textColor=primary,
        leading=16, spaceBefore=8, spaceAfter=4, alignment=align_body, fontName=bold_font,
    )
    body_style = ParagraphStyle(
        "Body", parent=base["Normal"], fontSize=10, textColor=primary,
        leading=15, spaceAfter=6, alignment=align_body, fontName=body_font,
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=body_style, leftIndent=16 if not rtl else 0,
        rightIndent=16 if rtl else 0, bulletIndent=4, spaceAfter=3,
    )

    def _para_text(s: str) -> str:
        """Prépare le contenu d'un Paragraph selon le mode (RTL → shape + bidi, sans HTML)."""
        if rtl:
            return _shape_arabic(_strip_inline_plain(s))
        return _strip_inline(s)

    story: list = []

    # En-tête de marque (latin → toujours en Helvetica).
    story.append(Paragraph("LecturAI", title_style))
    if subject and subject.strip().lower() != "general":
        story.append(Paragraph(_para_text(subject), subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.2, color=brand, spaceAfter=10))

    raw = lesson_markdown or ""
    # ``skip_until_level`` : niveau du heading qui a déclenché le skip (1, 2 ou 3).
    # On reprend le rendu dès qu'on rencontre un heading de niveau ≤ skip_until_level.
    skip_until_level: Optional[int] = None

    for line in raw.splitlines():
        s = line.rstrip()

        # Détection / sortie de la zone "skip" (quiz, flashcards, etc.).
        if s.startswith("# ") or s.startswith("## ") or s.startswith("### "):
            level = 1 if s.startswith("# ") else (2 if s.startswith("## ") else 3)
            heading_text = s.lstrip("#").strip()
            if skip_until_level is not None and level <= skip_until_level:
                skip_until_level = None  # on quitte la zone skippée
            if _is_skip_heading(heading_text):
                skip_until_level = level
                continue
            if skip_until_level is not None:
                continue
        elif skip_until_level is not None:
            continue

        if not s.strip():
            story.append(Spacer(1, 4))
            continue
        if s.strip() in ("---", "***", "___"):
            story.append(HRFlowable(width="100%", thickness=0.6, color=muted, spaceBefore=6, spaceAfter=6))
            continue
        if s.startswith("### "):
            story.append(Paragraph(_para_text(s[4:].strip()), h3_style))
            continue
        if s.startswith("## "):
            story.append(Paragraph(_para_text(s[3:].strip()), h2_style))
            continue
        if s.startswith("# "):
            story.append(Paragraph(_para_text(s[2:].strip()), h1_style))
            continue
        stripped = s.lstrip()
        if stripped.startswith(("- ", "* ", "• ")):
            content = stripped[2:].strip()
            bullet = "•"
            if rtl:
                # En RTL on met la puce après le texte (sera placée à droite par alignement).
                story.append(Paragraph(_para_text(content) + " " + bullet, bullet_style))
            else:
                story.append(Paragraph(bullet + " " + _para_text(content), bullet_style))
            continue
        m_num = re.match(r"^(\d+)[\.\)]\s+(.+)$", stripped)
        if m_num:
            num = m_num.group(1)
            text = m_num.group(2)
            if rtl:
                story.append(Paragraph(_para_text(text) + f" .{num}", bullet_style))
            else:
                story.append(Paragraph(f"{num}. " + _para_text(text), bullet_style))
            continue
        story.append(Paragraph(_para_text(s.strip()), body_style))

    # Compteur de pages via callback (reportlab n'expose pas directement le total final).
    page_counter = [0]

    def _on_page(canvas, _doc):
        page_counter[0] += 1

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue(), max(1, page_counter[0])
