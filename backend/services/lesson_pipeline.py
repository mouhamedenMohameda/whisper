"""Façade plateforme-agnostique pour la génération de fiches de cours.

But : exposer une surface stable que **WhatsApp** et **Telegram** consomment.
Implémentation actuelle = re-export de fonctions pures déjà présentes dans
``whatsapp.processor`` et ``whatsapp.simple_pdf``. Cette indirection permet de
migrer plus tard l'implémentation hors de ``whatsapp.processor`` sans casser
les appelants (Telegram en premier).

Fonctions exposées :
  - ``build_lesson_for_job(job)``        : transcript → markdown leçon (LLM + débit MRU).
  - ``build_lesson_pdf_bytes(md, ...)``  : markdown → PDF (FR/AR).
  - ``detect_subject_from_text(text)``   : heuristique matière à partir du transcript.
  - ``extract_quiz(lesson_md)``          : extrait questions/options/correct du markdown.
  - ``resolve_correct_option(...)``      : matching robuste user_answer ↔ option correcte.

Si une fonction WA est ajoutée/renommée, mettre à jour ce module — pas les appelants.
"""

from __future__ import annotations

from whatsapp.processor import (
    _build_lesson_for_job as build_lesson_for_job,
    _detect_subject_from_text as detect_subject_from_text,
    _extract_quiz as extract_quiz,
    _resolve_correct_option as resolve_correct_option,
)
from whatsapp.simple_pdf import build_lesson_pdf_bytes

__all__ = [
    "build_lesson_for_job",
    "build_lesson_pdf_bytes",
    "detect_subject_from_text",
    "extract_quiz",
    "resolve_correct_option",
]
