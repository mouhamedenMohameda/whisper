from __future__ import annotations

import re
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import auth_required, require_wallet_user
from models import AppUserFeedback, TranscriptionJob, TranscriptionJobRating, User

router = APIRouter(tags=["feedback"])

_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _assert_job_access_for_feedback(job: TranscriptionJob, _auth: Optional[User]) -> None:
    """Aligné sur la logique d’accès de ``GET /transcribe-jobs/{id}``."""
    if auth_required():
        if _auth is None:
            raise HTTPException(status_code=401, detail="Connexion requise.")
        if _auth.id != job.user_id:
            raise HTTPException(status_code=403, detail="Accès à cette tâche refusé.")
    elif job.user_id is not None:
        raise HTTPException(
            status_code=403,
            detail="Connexion requise pour les tâches liées à un compte.",
        )


def _normalize_feedback_ui_locale(raw: str) -> str:
    s = (raw or "").strip().lower().replace("-", "_")
    return "ar" if s.startswith("ar") else "fr"


class TranscriptionRatingBody(BaseModel):
    job_public_ids: list[str] = Field(default_factory=list)
    stars: int = Field(..., ge=1, le=5)

    @field_validator("job_public_ids")
    @classmethod
    def validate_job_ids(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in v or []:
            s = (raw or "").strip().lower()
            if not s or s in seen:
                continue
            if not _JOB_ID_RE.match(s):
                raise ValueError("Identifiant de tâche invalide.")
            seen.add(s)
            out.append(s)
            if len(out) > 24:
                raise ValueError("Trop de tâches en une seule demande (max 24).")
        if not out:
            raise ValueError("Indique au moins une tâche de transcription terminée.")
        return out


class AppFeedbackBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    ui_locale: str = Field(default="fr", max_length=16)


@router.post("/feedback/transcription-rating")
def post_transcription_rating(
    body: TranscriptionRatingBody,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
):
    """Note 1–5 pour une ou plusieurs tâches terminées (même note appliquée à chaque id)."""
    uid: Optional[int] = _auth.id if _auth is not None else None

    for public_id in body.job_public_ids:
        job = db.scalars(select(TranscriptionJob).where(TranscriptionJob.public_id == public_id)).first()
        if job is None:
            raise HTTPException(status_code=404, detail=f"Tâche introuvable ({public_id[:8]}…).")
        _assert_job_access_for_feedback(job, _auth)
        if job.status != "done":
            raise HTTPException(
                status_code=409,
                detail="La transcription n’est pas encore terminée — note impossible pour l’instant.",
            )

        existing = db.scalars(
            select(TranscriptionJobRating).where(TranscriptionJobRating.transcription_job_id == job.id),
        ).first()
        if existing is not None:
            existing.stars = body.stars
            if uid is not None:
                existing.user_id = uid
        else:
            db.add(
                TranscriptionJobRating(
                    transcription_job_id=job.id,
                    user_id=uid,
                    stars=body.stars,
                ),
            )
    db.commit()
    return {"ok": True, "count": len(body.job_public_ids)}


@router.post("/feedback/suggestion")
def post_app_feedback_suggestion(
    body: AppFeedbackBody,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
):
    """Idées d’évolution ou retours globaux (hors note par transcription)."""
    if auth_required() and _auth is None:
        raise HTTPException(status_code=401, detail="Connexion requise pour envoyer un message.")

    ui = _normalize_feedback_ui_locale(body.ui_locale)[:16]
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message vide.")

    uid: Optional[int] = _auth.id if _auth is not None else None
    row = AppUserFeedback(user_id=uid, message=msg[:8000], ui_locale=ui)
    db.add(row)
    db.commit()
    return {"ok": True}
