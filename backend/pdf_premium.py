"""Génération de PDF Premium (Elite) — pipeline LaTeX partagé entre web et WhatsApp.

Pipeline :
  1. LLM convertit le markdown de la fiche en code LaTeX (style Harvard/Oxford).
  2. ``pdflatex`` compile le ``.tex`` en PDF (2 passes pour la TOC).
  3. Le binaire PDF est renvoyé avec son nombre de pages.

Pré-requis serveur :
  - ``pdflatex`` installé (paquet ``texlive-full`` ou ``texlive-latex-extra``).
  - Police libertine, biolinum, inconsolata (incluses dans texlive-fonts-extra).

Si LaTeX n'est pas disponible, ``PremiumPdfUnavailable`` est levé — appelant peut fallback
sur le rendu reportlab simple (``whatsapp/simple_pdf.py``).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class PremiumPdfError(Exception):
    """Erreur récupérable pendant la génération PDF Premium."""


class PremiumPdfUnavailable(PremiumPdfError):
    """Le sous-système LaTeX n'est pas configuré sur ce serveur (pas de pdflatex)."""


LATEX_PREMIUM_SYSTEM_PROMPT = """You are an Elite Academic Publisher and master LaTeX Typographer.
The user is paying for an ULTRA-PREMIUM experience. The document must be visually stunning, perfectly balanced, and evoke the quality of an expensive textbook from a top-tier university (Harvard/Oxford style).

# 🚨 CARDINAL RULE — FIDELITY OF CONTENT (READ TWICE):
You are a *typographer*, NOT an editor or summarizer.
- **TRANSLATE THE MARKDOWN TO LATEX FAITHFULLY** — preserve EVERY paragraph, EVERY bullet, EVERY example, EVERY sentence.
- DO NOT shorten, DO NOT condense, DO NOT "professionalize" by removing content.
- If the markdown has 12 paragraphs in section 3, the LaTeX must have 12 paragraphs in section 3.
- If a markdown bullet has 5 lines of explanation, render those 5 lines in full.
- The LaTeX output should be at least as long (in word count) as the markdown input — usually LONGER because LaTeX markup adds verbosity.
- Tcolorbox / lettrine / titlepage are STYLING, not replacements for content. Use them to *frame* the content, never to *replace* it.
- VIOLATION OF THIS RULE = TASK FAILURE.

# Visual & Typographic Requirements:
- Use 'article' class with [11pt, a4paper].
- Essential Packages:
    - 'geometry' (left=2cm, right=2cm, top=2.5cm, bottom=2.5cm).
    - 'libertine', 'biolinum', 'inconsolata'.
    - 'lettrine', 'microtype', 'xcolor' (dvipsnames), 'titlesec', 'fancyhdr', 'booktabs', 'multicol', 'enumitem', 'tabularx'.
    - 'tcolorbox' (v4+) with 'skins' and 'breakable'.

# Custom Elite Styles (MUST use these definitions in the preamble):
1. \\definecolor{primary}{HTML}{1e1b4b} \\definecolor{accent}{HTML}{4f46e5} \\definecolor{bglight}{HTML}{f8fafc}
2. \\newtcolorbox{DefinitionBox}[1]{colback=accent!5, colframe=accent, fonttitle=\\bfseries\\sffamily, title=#1, arc=4pt, breakable, enhanced, shadow={1mm}{-1mm}{0mm}{accent!20}}
3. \\newtcolorbox{TakeawayBox}{colback=bglight, colframe=accent, leftrule=4pt, rightrule=0pt, toprule=0pt, bottomrule=0pt, sharp corners, breakable}
4. \\newtcolorbox{WarningBox}[1]{colback=red!5, colframe=red!75!black, fonttitle=\\bfseries\\sffamily, title=#1, arc=4pt, breakable}

# Layout Rules:
- COVER PAGE: Use \\begin{titlepage}, \\centering, \\vspace*{4cm}, Massive Title in \\Huge\\bfseries\\color{primary}, \\vfill, "Édition Premium par LecturAI" in \\small\\scshape, \\end{titlepage}.
- TABLES: ALWAYS use 'tabularx' with width '\\linewidth' and at least one 'X' column for long text to prevent margin overflow. Use 'booktabs' rules (\\toprule, \\midrule, \\bottomrule).
- HEADERS: Fancyhdr with 'LecturAI Elite Series' and section names.
- SECTIONING: Large Biolinum Bold fonts with a thin rule below.
- ENUMERATION: Use enumitem for tight, professional lists.
- TYPOGRAPHY: Use \\lettrine for the first letter of the introduction.
- BOXES: Use DefinitionBox{Définition} for definitions, TakeawayBox for key insights, WarningBox{Attention} for common errors — but the surrounding paragraphs MUST stay developed.

# Content Mapping (markdown → LaTeX, do this for EVERY section, no exceptions) :
- markdown ## title  →  \\section{title}
- markdown ### sub   →  \\subsection{sub}
- markdown paragraph →  full \\par paragraph (preserve every sentence)
- markdown **bold**  →  \\textbf{...}

- markdown bullet lists (ANY of `*`, `-`, `+` at start of line) → \\begin{itemize}[leftmargin=*] \\item ... \\end{itemize}.
  *Nested* lists (a sub-bullet indented or prefixed by `+`) → nested \\begin{itemize}.
  EXAMPLE :
  ```
  * Formules clés :
    + Moyenne : x̄ = Σxi / n
    + Variance : σ² = Σ(xi - x̄)² / (n-1)
  * Seuils typiques :
    + Corrélation : r > 0.7 = fort
  ```
  →
  \\begin{itemize}[leftmargin=*]
    \\item \\textbf{Formules clés :}
      \\begin{itemize}
        \\item Moyenne : $\\bar{x} = \\frac{\\sum x_i}{n}$
        \\item Variance : $\\sigma^2 = \\frac{\\sum (x_i - \\bar{x})^2}{n-1}$
      \\end{itemize}
    \\item \\textbf{Seuils typiques :}
      \\begin{itemize}
        \\item Corrélation : $r > 0.7$ = fort
      \\end{itemize}
  \\end{itemize}

- markdown pipe table → ALWAYS render with tabularx booktabs. NEVER render a markdown table as inline flow text.
  EXAMPLE :
  ```
  | Concept | À retenir |
  | ------- | --------- |
  | Moyenne | Tendance centrale |
  | Variance | Dispersion |
  ```
  →
  \\begin{tabularx}{\\linewidth}{lX}
    \\toprule
    \\textbf{Concept} & \\textbf{À retenir} \\\\
    \\midrule
    Moyenne & Tendance centrale \\\\
    Variance & Dispersion \\\\
    \\bottomrule
  \\end{tabularx}
- Bloc commençant par "Erreur fréquente :" → \\begin{WarningBox}{Erreur fréquente} X \\end{WarningBox}
- Bloc commençant par "À retenir :" → \\begin{TakeawayBox} \\textbf{À retenir :} X \\end{TakeawayBox}
- Bloc commençant par "Astuce :" → \\begin{TakeawayBox} \\textbf{Astuce :} X \\end{TakeawayBox}
- Bloc commençant par "Test rapide :" → \\begin{TakeawayBox} \\textbf{Test rapide :} X \\end{TakeawayBox} (encadré, pas une section)
- Bloc commençant par "Définition :" → \\begin{DefinitionBox}{Définition} X \\end{DefinitionBox}
- Dans le quiz, une option suivie de " (correct)" est la bonne réponse → la mettre en \\textbf{...} et noter explicitement "Réponse correcte" en gras.
- "Pour aller plus loin" / sujets à approfondir → rendre TELS QUELS (liste complète).

# ⚠️ EXCLUSIONS — sections à NE PAS rendre :
- Si le markdown contient des sections quiz / qcm / questionnaire / flashcard / cartes mémoire,
  **IGNORE-LES TOTALEMENT** : ne crée pas de \\section LaTeX correspondante, ne reproduis pas
  les questions ni les cartes. Saute directement à la section suivante du markdown.
  (Le bot WhatsApp a une commande /quiz dédiée pour ces contenus, ils n'ont rien à faire dans le PDF.)

# Technical Rigor:
- ESCAPE ALL SPECIAL CHARACTERS in body text: (%, &, $, #, _, {, }, ~, ^, \\\\).
- If text contains equations, use AMS-LaTeX.
- Ensure perfect French support via babel.

# Greek letters & math symbols — CRITICAL :
- EVERY Greek letter (μ, σ, χ, α, β, γ, π, λ, ω, Σ, Π, Δ, θ, ρ, ν, ε, ξ, etc.) MUST appear in math mode :
  `$\\mu$`, `$\\sigma$`, `$\\chi$`, `$\\alpha$`, `$\\sigma^2$`, `$\\sum$`, `$\\bar{x}$`, etc.
- NEVER leave Greek as Unicode characters in body text — libertine drops them silently and you get formulas like " = (x1 + x2)/n" with a missing μ.
- EVERY mathematical formula (even inline) MUST be wrapped in `$...$` or `$$...$$`.
- Subscripts/superscripts use `_` and `^` in math mode : `x_i`, `\\bar{x}`, `\\sigma^2`.

- DANGER: DO NOT WRAP THE OUTPUT IN MARKDOWN CODE BLOCKS (```latex).
- OUTPUT ONLY THE RAW LATEX SOURCE CODE starting with \\documentclass. No backticks. No commentary before or after.
"""


# Sections markdown à exclure du PDF (rendu Elite *et* fallback simple_pdf).
# La logique strip ici reflète exactement celle de ``whatsapp/simple_pdf.py``.
_PDF_SKIP_SECTION_KEYWORDS = (
    "quiz", "qcm", "questionnaire",
    "flashcard", "flash card", "carte mémoire", "cartes mémoire", "cartes-mémoire",
    "اختبار", "أسئلة", "بطاقات",
)


def _is_skip_heading(heading_text: str) -> bool:
    h = (heading_text or "").lower()
    return any(k in h for k in _PDF_SKIP_SECTION_KEYWORDS)


# Emojis → équivalents texte pour LaTeX (libertine/pdflatex ne rend pas les emojis Unicode).
# On retire les décoratifs et on remplace ceux qui portent du sens par un libellé textuel.
_EMOJI_REPLACEMENTS = {
    # Tags d'importance (décoratifs — l'info reste dans le contenu)
    "🔥": "",
    "⭐": "",
    "💡": "",
    "⚠️": "",
    "⚠": "",
    "📌": "",
    "🧪": "",
    "🎯": "",
    "📐": "",
    "🗺️": "",
    "🗺": "",
    "🧠": "",
    "🚨": "",
    "📋": "",
    "📚": "",
    "🎓": "",
    "❌": "[X]",
    "✓": "(ok)",
    # ✅ porte le sens critique pour le quiz : on le remplace par un marqueur texte
    # que le prompt LaTeX reconnaît comme "réponse correcte".
    "✅": " (correct)",
}


def strip_emojis_for_latex(md: str) -> str:
    """Retire les emojis Unicode d'un markdown avant de l'envoyer au pipeline LaTeX.

    Pourquoi : ``pdflatex`` + police ``libertine`` n'ont pas de glyphs pour la plupart des
    emojis. La présence d'emojis dans la sortie LLM → compile fail → 3 retries → fallback
    reportlab simple. On les remplace côté markdown avant la conversion.
    """
    if not md:
        return md
    out = md
    for emoji, replacement in _EMOJI_REPLACEMENTS.items():
        out = out.replace(emoji, replacement)
    return out


def strip_pdf_only_sections(markdown_text: str) -> str:
    """Retourne le markdown sans les sections quiz/flashcards (pour rendu PDF).

    Le contenu strippé reste disponible dans la base (commande ``/quiz`` du bot WhatsApp).
    """
    if not markdown_text:
        return markdown_text
    out_lines: list[str] = []
    skip_until_level: Optional[int] = None
    for line in markdown_text.splitlines():
        if line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
            level = 1 if line.startswith("# ") else (2 if line.startswith("## ") else 3)
            heading_text = line.lstrip("#").strip()
            if skip_until_level is not None and level <= skip_until_level:
                skip_until_level = None
            if _is_skip_heading(heading_text):
                skip_until_level = level
                continue
            if skip_until_level is not None:
                continue
        elif skip_until_level is not None:
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _clean_latex_code(raw: str) -> str:
    """Nettoie la sortie LLM : retire backticks, isole à partir de ``\\documentclass``."""
    latex_code = (raw or "").strip()
    if "```" in latex_code:
        match = re.search(r"```(?:latex)?\n?(.*?)\n?```", latex_code, re.DOTALL)
        if match:
            latex_code = match.group(1).strip()
        else:
            latex_code = re.sub(r"```[a-z]*\n?", "", latex_code).replace("```", "")
    if "\\documentclass" in latex_code and not latex_code.startswith("\\documentclass"):
        latex_code = latex_code[latex_code.find("\\documentclass"):]
    return latex_code


def _pdflatex_available() -> bool:
    try:
        subprocess.run(["pdflatex", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _count_pdf_pages_from_pdflatex_log(log_output: str) -> Optional[int]:
    """Parse la ligne ``Output written on .../lesson.pdf (N pages, X bytes).`` de pdflatex."""
    if not log_output:
        return None
    m = re.search(r"Output written on .*?\.pdf\s*\((\d+)\s*pages?", log_output)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            return None
    return None


def _count_pdf_pages_from_bytes(pdf_bytes: bytes) -> int:
    """Fallback : scan des objets ``/Type /Page`` (échoue si le PDF a des streams compressés)."""
    if not pdf_bytes:
        return 1
    matches = re.findall(rb"/Type\s*/Page[\s/\r\n]", pdf_bytes)
    return max(1, len(matches))


def generate_premium_pdf_bytes(
    *,
    lesson_markdown: str,
    subject: str,
    language: str,
    api_key: str,
    model: Optional[str] = None,
    max_attempts: int = 3,
) -> tuple[bytes, int, str]:
    """Génère le PDF Premium et retourne ``(pdf_bytes, page_count, latex_source)``.

    Lève :
      - ``PremiumPdfUnavailable`` si ``pdflatex`` n'est pas installé.
      - ``PremiumPdfError`` si la compilation échoue après ``max_attempts`` tentatives.

    L'appelant gère la facturation (latex_conversion_billed_mru côté web, par-page côté WhatsApp).
    """
    if not _pdflatex_available():
        raise PremiumPdfUnavailable(
            "pdflatex absent du serveur — installer texlive (apt install texlive-full)."
        )

    # Import différé pour ne pas alourdir l'import-time du module si Groq n'est pas dispo.
    from groq import Groq
    from course_from_transcript import _groq_chat

    client = Groq(api_key=api_key)
    model_id = model or os.getenv("GROQ_LATEX_MODEL", "llama-3.3-70b-versatile")

    # Strip les sections quiz/flashcards AVANT d'envoyer au LLM : ceinture + bretelles, ne
    # dépend pas de la docilité du LLM à suivre l'instruction d'exclusion du prompt.
    markdown_for_pdf = strip_pdf_only_sections(lesson_markdown or "")
    # Strip les emojis Unicode (libertine/pdflatex ne les rend pas → compile fail).
    markdown_for_pdf = strip_emojis_for_latex(markdown_for_pdf)
    md_words = max(1, len(markdown_for_pdf.split()))
    user_prompt = (
        f"Subject: {subject}\nLanguage: {language}\n\n"
        f"⚠️ The markdown below contains *{md_words} words* across multiple sections. "
        f"Your LaTeX output MUST preserve every paragraph, bullet and example. "
        f"A faithful conversion should yield at least *{md_words} words* of body text in LaTeX "
        f"(typesetting commands like \\textbf, \\section etc. are on top of that).\n\n"
        f"MARKDOWN LESSON:\n{markdown_for_pdf}"
    )

    # max_tokens : Groq llama-3.3-70b supporte 32k output. Pour une fiche dense on a besoin
    # d'à peu près 1.5× le nombre de tokens du markdown (LaTeX est plus verbeux).
    md_tokens_est = max(2048, min(int(md_words * 1.5) + 1500, 32000))

    last_error = ""
    last_latex = ""
    for attempt in range(max_attempts):
        try:
            latex_raw, _, _ = _groq_chat(
                client,
                model=model_id,
                system=LATEX_PREMIUM_SYSTEM_PROMPT,
                user=user_prompt + (
                    "\n\nIMPORTANT: Your previous attempt failed to compile. "
                    "Please double check for unescaped special characters like &, %, $, #, _ outside of math mode."
                    if attempt > 0 else ""
                ),
                max_tokens=md_tokens_est,
                temperature=0.2 + (attempt * 0.1),
            )
        except Exception as exc:
            last_error = f"Pass {attempt + 1} (LLM) : {type(exc).__name__} — {str(exc)[:200]}"
            logger.warning("Premium PDF LLM attempt %d failed: %s", attempt + 1, exc)
            continue

        latex_code = _clean_latex_code(latex_raw)
        last_latex = latex_code

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tex_path = os.path.join(tmpdir, "lesson.tex")
                with open(tex_path, "w", encoding="utf-8") as f:
                    f.write(latex_code)

                # 1ʳᵉ passe : génère .aux .toc
                proc = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                pdf_path = os.path.join(tmpdir, "lesson.pdf")
                if not os.path.exists(pdf_path):
                    last_error = f"Pass {attempt + 1} compile fail: {proc.stdout[-500:]}"
                    logger.warning("Premium PDF compile attempt %d failed", attempt + 1)
                    continue

                # 2ᵉ passe pour résoudre TOC / refs croisées — on capture sa sortie pour le nb de pages.
                proc2 = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                with open(pdf_path, "rb") as fh:
                    pdf_data = fh.read()

                # Compte les pages : essai 1 = log pdflatex (fiable), essai 2 = scan binaire (fallback).
                pages = _count_pdf_pages_from_pdflatex_log(proc2.stdout or "")
                if pages is None:
                    pages = _count_pdf_pages_from_pdflatex_log(proc.stdout or "")
                if pages is None:
                    pages = _count_pdf_pages_from_bytes(pdf_data)
                logger.info("Premium PDF généré : %d pages, %d bytes", pages, len(pdf_data))
                return pdf_data, pages, latex_code
        except subprocess.TimeoutExpired:
            last_error = f"Pass {attempt + 1} : pdflatex timeout (90s)"
            logger.warning("Premium PDF pdflatex timeout attempt %d", attempt + 1)
        except FileNotFoundError as exc:
            raise PremiumPdfUnavailable(str(exc))
        except Exception as exc:
            last_error = f"Pass {attempt + 1} (compile) : {type(exc).__name__} — {str(exc)[:200]}"
            logger.exception("Premium PDF compile attempt %d failed", attempt + 1)

    raise PremiumPdfError(f"Échec après {max_attempts} tentatives. Dernière erreur : {last_error}")
