"""Analyse optionnelle transcript (sujet + synthèse Groq) — hors pipeline transcription.

Même route que la synthèse « après coup » dans l’UI : débit portefeuille après succès
(``groq_billed`` → ``debit_credits``), distinct du total transcription quand la synthèse
n’a pas été intégrée au passage ``/transcribe``.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from groq import APIError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from credits_wallet import debit_credits
from database import get_db
from deps import require_wallet_user
from groq_errors import http_detail_for_groq_api_error
from groq_transcript_intel import groq_infer_subject_and_deep_summary
from models import User
from pricing import billed_mru_to_wallet_units_debit, groq_billed, wallet_units_to_mru_display

logger = logging.getLogger(__name__)

router = APIRouter()


class TranscriptInsightRequest(BaseModel):
    transcript: str
    subject: str = "General"
    speech_language: str = Field(default="fr", description="fr | ar pour le style de sortie Groq")


@router.post("/transcript-insight")
async def transcript_insight(
    req: TranscriptInsightRequest,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
):
    if not (os.getenv("GROQ_API_KEY") or "").strip():
        raise HTTPException(
            status_code=500,
            detail="La clé technique du moteur d’analyse est manquante sur le serveur (variable d’environnement).",
        )
    plain = (req.transcript or "").strip()
    if len(plain) < 80:
        raise HTTPException(status_code=400, detail="Transcript trop court pour une analyse (minimum ~80 caractères).")

    sl = (req.speech_language or "fr").strip().lower()
    speech = "ar" if sl.startswith("ar") else "fr"

    try:
        intel = groq_infer_subject_and_deep_summary(
            transcript_plain=plain,
            user_subject_hint=req.subject,
            speech_language=speech,
        )
    except APIError as e:
        logger.warning("transcript_insight APIError", exc_info=True)
        raise HTTPException(status_code=502, detail=http_detail_for_groq_api_error(e)) from None
    if not intel:
        raise HTTPException(
            status_code=503,
            detail="L’analyse n’a pas pu aboutir (réponse vide ou illisible). Réessaie dans quelques instants.",
        )

    pt = int(intel.get("prompt_tokens") or 0)
    ct = int(intel.get("completion_tokens") or 0)
    groq_usd, groq_mru = groq_billed(pt, ct)

    payload: dict = {
        "inferred_subject": intel.get("inferred_subject") or "",
        "deep_summary": intel.get("deep_summary") or "",
        "groq_insight_applied": True,
        "groq_transcript_truncated": bool(intel.get("transcript_truncated")),
        "usage": {
            "groq_insight_prompt_tokens": pt,
            "groq_insight_completion_tokens": ct,
            "provider_usd_groq_insight_est": float(groq_usd),
            "billed_mru_groq_insight": float(groq_mru),
        },
    }
    charge_units = billed_mru_to_wallet_units_debit(groq_mru)
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
