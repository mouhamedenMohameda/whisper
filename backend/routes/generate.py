from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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
from rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

def get_localized_lesson_system_prompt(target_lang: str) -> str:
    is_ar = target_lang == "ar"
    
    # Titres des sections
    h1 = "1. العنوان" if is_ar else "1. TITRE"
    h2 = "2. مقدمة" if is_ar else "2. INTRODUCTION"
    h3 = "3. مسرد المفاهيم الأساسية" if is_ar else "3. GLOSSAIRE DES CONCEPTS CLÉS"
    h4 = "4. محتوى الدرس" if is_ar else "4. CORPS DU COURS"
    h5 = "5. جدول ملخص" if is_ar else "5. TABLEAU RÉCAPITULATIF"
    h6 = "6. اختبار تدريبي" if is_ar else "6. QUIZ D'ENTRAÎNEMENT"
    h7 = "7. بطاقات استذكار" if is_ar else "7. FLASHCARDS"
    h8 = "8. مواضيع للمراجعة اللاحقة" if is_ar else "8. SUJETS À RÉVISER ENSUITE"

    prompt = f"""You are an expert university tutor. A student recorded their professor's lecture and needs it transformed into a complete, clear, well-structured course they can study from.

Given the transcript below, produce a lesson with these sections:

## {h1}
   {"عنوان واضح وجذاب لهذا الدرس." if is_ar else "Un titre clair et engageant pour cette leçon."}

## {h2} (3-4 {"جمل" if is_ar else "phrases"})
   {"ضع السياق: ما هو موضوع هذا الدرس ولماذا هو مهم؟" if is_ar else "Mettez en contexte : de quoi parle cette leçon et pourquoi est-elle importante ?"}

## {h3}
   {"قم بإدراج كل مصطلح مهم تم ذكره، مع تعريف بسيط من سطر واحد." if is_ar else "Listez chaque terme important mentionné, avec une définition simple en une ligne."}
   {"التنسيق" if is_ar else "Format"} : **{"مصطلح" if is_ar else "Terme"}** — {"تعريف" if is_ar else "définition"}

## {h4} ({"القسم الرئيسي" if is_ar else "section principale"})
   {"قسم المحتوى إلى فصول/أقسام منطقية." if is_ar else "Divisez le contenu en chapitres/sections logiques."}
   {"لكل قسم" if is_ar else "Pour chaque section"} :
   - {"عنوان واضح" if is_ar else "Un titre clair"}
   - {"شرح بلغة بسيطة وسهلة للطالب" if is_ar else "Une explication dans un langage simple et adapté à l'étudiant"}
     ({"أعد شرح ما قاله الأستاذ بشكل أوضح إذا لزم الأمر" if is_ar else "(ré-expliquez ce que le professeur a dit plus clairement si nécessaire)"})
   - {"مثال واقعي ملموس لتوضيح المفهوم" if is_ar else "Un exemple concret du monde réel pour illustrer le concept"}
   - {"مربع '⚠️ خطأ شائع' إذا كان ذلك مناسبًا" if is_ar else "Un encadré '⚠️ Erreur commune' si pertinent"}

## {h5}
   {"جدول ماركداون من عمودين" if is_ar else "Un tableau markdown avec 2 colonnes"} : {"المفهوم" if is_ar else "Concept"} | {"ما يجب تذكره" if is_ar else "Ce qu'il faut retenir"}

## {h6} (5 {"أسئلة" if is_ar else "questions"})
   {"5 أسئلة متعددة الخيارات مع 4 خيارات لكل منها." if is_ar else "5 questions à choix multiples avec 4 options chacune."}
   {"حدد الإجابة الصحيحة بـ" if is_ar else "Marquez la réponse correcte avec"} ✅
   {"أضف شرحًا من جملة واحدة لكل إجابة." if is_ar else "Ajoutez une explication d'une phrase pour chaque réponse."}

## {h7} (10 {"بطاقات" if is_ar else "cartes"})
   {"التنسيق" if is_ar else "Format"} : Q: [{"سؤال" if is_ar else "question"}] / A: [{"إجابة" if is_ar else "réponse"}]
   {"غطي أهم النقاط من المحاضرة." if is_ar else "Couvrez les points les plus importants de la conférence."}

## {h8}
   {"3 مواضيع يجب على الطالب استكشافها للتعمق أكثر." if is_ar else "3 sujets que l'étudiant devrait explorer pour approfondir."}

Write in the language requested by the user when provided; otherwise, write in the same language as the transcript.
Be clear, pedagogical, and student-friendly.
Use markdown formatting throughout."""
    return prompt


# AMÉ 2 — Modifiers de prompt par matière pour adapter le ton, le format et les exemples.
# Clé = fragment normalisé du champ "subject" (minuscules, sans accent).
SUBJECT_MODIFIERS: dict[str, str] = {
    "math": (
        "When writing the lesson, include LaTeX-style formulas (using $...$ inline or $$...$$ for blocks) "
        "wherever mathematics appears. Structure proofs and derivations step by step with numbered lines."
    ),
    "physique": (
        "Include relevant physical formulas using $...$. Reference SI units and orders of magnitude. "
        "Illustrate each concept with a concrete physical experiment or real-world application."
    ),
    "chimie": (
        "Use standard chemical notation (molecular formulas, reaction equations with ⇌ arrows, state symbols). "
        "Emphasise safety notes where relevant and include worked stoichiometry examples."
    ),
    "droit": (
        "Reference legal articles, codes and jurisprudence mentioned in the lecture wherever applicable. "
        "Structure the lesson around legal definitions, principles, exceptions and case examples."
    ),
    "histoire": (
        "Include a concise chronological timeline (markdown table: Date | Event) covering the key dates "
        "from the lecture. Contextualise events politically, economically and socially."
    ),
    "economie": (
        "Represent economic relationships with simple graphs described in text (e.g. supply/demand). "
        "Always ground abstract models in real-world data or policy examples."
    ),
    "informatique": (
        "Include representative code snippets (fenced code blocks with language tags) to illustrate algorithms. "
        "Use Big-O notation when discussing complexity. Prefer concrete runnable examples."
    ),
    "medecine": (
        "Use standard medical terminology but always add a plain-language parenthetical explanation. "
        "Structure clinical content around: étiology, pathophysiology, clinical signs, diagnosis, treatment."
    ),
    "biologie": (
        "Use proper scientific nomenclature (genus *Italicised*). "
        "Connect molecular mechanisms to cellular and organism-level outcomes whenever possible."
    ),
}


def _subject_modifier(subject: str) -> str:
    """Retourne un modificateur de prompt adapté au champ 'subject', ou chaîne vide si aucun match."""
    normalized = subject.lower()
    for key, modifier in SUBJECT_MODIFIERS.items():
        if key in normalized:
            return modifier
    return ""



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
    job_public_id: Optional[str] = Field(
        default=None,
        description="ID public de la tâche de transcription associée (pour persistance).",
    )


@router.post("/generate")
@limiter.limit("30/hour")
async def generate_lesson(
    request: Request,
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
    
    # AMÉ : Prompt localisé selon la langue cible
    lesson_system_prompt = get_localized_lesson_system_prompt(target_lang)
    
    lesson_system_prompt += (
        f"\n\nIMPORTANT: Write the entire lesson in {lang_name} (no English). "
        + "For the quiz section, keep options labeled exactly A) B) C) D) (Latin letters), "
        + "mark the correct choice with ✅, and prefix each answer explanation with "
        + f"'{expl_label}:'."
    )

    # AMÉ 2 — Injecter le modificateur de prompt selon la matière
    subject_mod = _subject_modifier(req.subject or "")
    if subject_mod:
        lesson_system_prompt += f"\n\nSUBJECT-SPECIFIC INSTRUCTIONS: {subject_mod}"
        logger.debug("/api/generate: subject modifier applied for subject=%r", req.subject)

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

    if req.job_public_id:
        # Tente de sauvegarder le cours en base pour les consultations futures (Amélioration : consulter sans regénérer).
        from sqlalchemy import select
        from models import TranscriptionJob
        job = db.scalars(select(TranscriptionJob).where(TranscriptionJob.public_id == req.job_public_id)).first()
        if job:
            if _auth is None or job.user_id == _auth.id:
                job.lesson_markdown = lesson_markdown
                db.add(job)
                db.commit()


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
