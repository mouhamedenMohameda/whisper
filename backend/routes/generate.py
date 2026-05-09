from __future__ import annotations

import os
from typing import Annotated, Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from credits_wallet import debit_credits
from database import get_db
from deps import require_wallet_user
from models import User
from pricing import billed_mru_to_wallet_units_debit, claude_billed, wallet_units_to_mru_display

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

Write in the same language as the transcript.
Be clear, pedagogical, and student-friendly.
Use markdown formatting throughout."""


class GenerateRequest(BaseModel):
    transcript: str
    subject: str = "General"


@router.post("/generate")
async def generate_lesson(
    req: GenerateRequest,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="La clé technique de génération de cours est manquante sur le serveur.",
        )

    if not req.transcript or len(req.transcript.strip()) < 50:
        raise HTTPException(status_code=400, detail="Transcript too short to generate a lesson.")

    client = anthropic.Anthropic(api_key=api_key)

    user_message = f"""Subject: {req.subject}

TRANSCRIPT:
{req.transcript}

Please generate a complete, structured lesson from this lecture transcript."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            system=LESSON_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        lesson_markdown = message.content[0].text

        inp = message.usage.input_tokens
        out = message.usage.output_tokens
        claude_usd, claude_mru = claude_billed(inp, out)

        payload = {
            "lesson": lesson_markdown,
            "subject": req.subject,
            "input_tokens": inp,
            "output_tokens": out,
            "usage": {
                "claude_input_tokens": inp,
                "claude_output_tokens": out,
                "claude_total_tokens": inp + out,
                "provider_usd_claude": round(claude_usd, 8),
                "billed_mru_claude": round(claude_mru, 6),
            },
        }
        charge_units = billed_mru_to_wallet_units_debit(claude_mru)
        new_bal, charged = debit_credits(db, _auth, charge_units)
        if new_bal is not None:
            payload["wallet"] = {
                "balance_units": new_bal,
                "spent_units": charged,
                "balance_mru": wallet_units_to_mru_display(new_bal),
                "spent_mru_this_request": wallet_units_to_mru_display(charged),
            }
            payload["credits"] = payload["wallet"]
        return JSONResponse(payload)

    except anthropic.APIError:
        raise HTTPException(
            status_code=500,
            detail="La génération du cours n'a pas pu aboutir. Réessaie dans quelques instants.",
        ) from None
