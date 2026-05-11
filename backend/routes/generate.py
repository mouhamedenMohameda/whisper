from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from groq import APIError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from course_from_transcript import run_course_pipeline
from groq_errors import http_detail_for_groq_api_error
from credits_wallet import debit_credits
from database import get_db
from deps import require_wallet_user
from models import User
from pricing import billed_mru_to_wallet_units_debit, groq_billed, wallet_units_to_mru_display

logger = logging.getLogger(__name__)

router = APIRouter()

LESSON_SYSTEM_PROMPT = """You are an expert university tutor. A student recorded their professor's lecture and needs it transformed into a complete, clear, well-structured course they can study from.

Given the transcript below, produce a lesson with these sections:

## 1. TITLE
   A clear, engaging title for this lesson.

## 2. INTRODUCTION (3-4 sentences)
   Set the context: what is this lesson about and why does it matter?

## 3. KEY CONCEPTS GLOSSARY
   List every important term mentioned, with a simple 1-line definition.
   Format: **Term** — definition

## 4. LESSON BODY (main section)
   Divide the content into logical chapters/sections.
   For each section:
   - A clear heading
   - An explanation in simple, student-friendly language
     (re-explain what the professor said more clearly if needed)
   - A concrete real-world example to illustrate the concept
   - A "⚠️ Common mistake" box if relevant

## 5. SUMMARY TABLE
   A markdown table with 2 columns: Concept | What to remember

## 6. PRACTICE QUIZ (5 questions)
   5 multiple-choice questions with 4 options each.
   Mark the correct answer with ✅
   Add a 1-sentence explanation for each answer.

## 7. FLASHCARDS (10 cards)
   Format: Q: [question] / A: [answer]
   Cover the most important points from the lecture.

## 8. WHAT TO REVIEW NEXT
   3 topics the student should explore to go deeper.

Write in the language requested by the user when provided; otherwise, write in the same language as the transcript.
Be clear, pedagogical, and student-friendly.
Use markdown formatting throughout."""


class GenerateRequest(BaseModel):
    transcript: str
    subject: str = "General"
    language: Optional[str] = Field(
        default=None,
        description="Langue de sortie souhaitée (UI) : 'fr' par défaut, 'ar' si sélectionné.",
    )
    transcript_mixed_view: Optional[dict[str, Any]] = Field(
        default=None,
        description="Repli : blocs + whisper_reliability si asr_passages_annotated absent.",
    )
    asr_passages_annotated: Optional[list[Any]] = Field(
        default=None,
        description="Passages Whisper avec reliability (réponse /transcribe) — entrée canonique du collage.",
    )


@router.post("/generate")
async def generate_lesson(
    req: GenerateRequest,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
):
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="La clé technique du moteur de cours est manquante sur le serveur (variable d’environnement).",
        )

    if not req.transcript or len(req.transcript.strip()) < 50:
        raise HTTPException(status_code=400, detail="Transcript too short to generate a lesson.")

    model = (os.getenv("GROQ_GENERATE_MODEL") or "").strip() or "llama-3.3-70b-versatile"
    try:
        max_tokens = int((os.getenv("GROQ_GENERATE_MAX_TOKENS") or "8192").strip())
    except ValueError:
        max_tokens = 8192
    max_tokens = max(256, min(max_tokens, 32768))

    target_lang = "ar" if (req.language or "").strip().lower().startswith("ar") else "fr"
    lang_name = "Arabic" if target_lang == "ar" else "French"
    expl_label = "تفسير" if target_lang == "ar" else "Explication"
    lesson_system_prompt = (
        LESSON_SYSTEM_PROMPT
        + f"\n\nIMPORTANT: Write the entire lesson in {lang_name} (no English). "
        + "For the quiz section, keep options labeled exactly A) B) C) D) (Latin letters), "
        + "mark the correct choice with ✅, and prefix each answer explanation with "
        + f"'{expl_label}:'."
    )

    try:
        lesson_markdown, inp, out, pipeline_meta = run_course_pipeline(
            api_key=api_key,
            subject=req.subject,
            transcript=req.transcript,
            asr_passages_annotated=req.asr_passages_annotated,
            transcript_mixed_view=req.transcript_mixed_view,
            lesson_system_prompt=lesson_system_prompt,
            model=model,
            max_tokens_lesson=max_tokens,
        )
    except APIError as e:
        body = getattr(e, "body", None)
        logger.warning("Groq /generate pipeline APIError message=%s body=%s", e, body)
        raise HTTPException(status_code=502, detail=http_detail_for_groq_api_error(e)) from None
    except ValueError as e:
        if str(e).strip() == "missing_asr_annotations":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Annotations ASR manquantes : renvoie `asr_passages_annotated` (réponse transcription) "
                    "ou `transcript_mixed_view` avec blocs `whisper_reliability`. "
                    "Relance une transcription avec l’app à jour, ou recharge la session depuis l’historique."
                ),
            ) from None
        if str(e).strip() == "empty_lesson":
            logger.warning("Groq /generate: modèle a renvoyé un cours vide après collage + génération.")
            raise HTTPException(
                status_code=502,
                detail="Le modèle a renvoyé un cours vide. Réessaie ; si ça persiste, raccourcis le transcript ou ajuste GROQ_GENERATE_MODEL.",
            ) from None
        logger.warning("Groq /generate pipeline ValueError: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="La génération du cours n'a pas pu aboutir. Réessaie dans quelques instants.",
        ) from None

    groq_usd, groq_mru = groq_billed(inp, out)

    charge_units = billed_mru_to_wallet_units_debit(groq_mru)
    new_bal, charged = debit_credits(db, _auth, charge_units)

    payload = {
        "lesson": lesson_markdown,
        "subject": req.subject,
        "input_tokens": inp,
        "output_tokens": out,
        "generation_pipeline": pipeline_meta,
        "usage": {
            "groq_input_tokens": inp,
            "groq_output_tokens": out,
            "groq_total_tokens": inp + out,
            "provider_usd_groq": float(groq_usd),
            "billed_mru_groq": float(groq_mru),
            "debit_wallet_units": int(charged),
        },
    }
    if groq_mru > 0 and charged <= 0:
        logger.warning(
            "/api/generate: coût cours estimé %.6f MRU mais débit portefeuille = 0 (user=%s, units=%s).",
            groq_mru,
            _auth.id if _auth else None,
            charge_units,
        )
    if new_bal is not None:
        payload["wallet"] = {
            "balance_units": new_bal,
            "spent_units": charged,
            "balance_mru": wallet_units_to_mru_display(new_bal),
            "spent_mru_this_request": wallet_units_to_mru_display(charged),
        }
        payload["credits"] = payload["wallet"]
    return JSONResponse(payload)
