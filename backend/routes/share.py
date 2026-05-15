"""Partage public d'une leçon générée — URL non-listée (token aléatoire).

Mécanique :
  - L'utilisateur (propriétaire du job) active le partage → on génère un token URL-safe de 32 octets.
  - L'URL ``/c/<token>`` est publique (sans auth). Le frontend la sert et propose un CTA d'inscription.
  - Le propriétaire peut désactiver à tout moment : ``public_share_token`` revient à NULL.

Garde-fous :
  - Aucune identité (email/NNI/WhatsApp) n'est exposée dans la réponse publique.
  - Le compteur de vues est best-effort (pas de lock — pertes ponctuelles tolérées).
  - Le token est généré par ``secrets.token_urlsafe(32)`` → non énumérable.
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from credits_wallet import utc_now
from database import get_db
from deps import require_user
from models import TranscriptionJob, User

router = APIRouter(tags=["share"])


def _public_share_base_url() -> str:
    """Base d'URL utilisée pour bâtir le lien à partager (sinon le frontend le calculera côté client)."""
    return (os.getenv("PUBLIC_APP_BASE_URL") or "").strip().rstrip("/")


def _share_url(token: str) -> Optional[str]:
    base = _public_share_base_url()
    if not base or not token:
        return None
    return f"{base}/c/{token}"


def _owner_job_or_404(db: Session, job_public_id: str, user: User) -> TranscriptionJob:
    if not job_public_id:
        raise HTTPException(status_code=404, detail="Leçon introuvable.")
    job = db.query(TranscriptionJob).filter(TranscriptionJob.public_id == job_public_id).one_or_none()
    if job is None or (job.user_id is not None and job.user_id != user.id):
        raise HTTPException(status_code=404, detail="Leçon introuvable.")
    if not (job.lesson_markdown or "").strip():
        raise HTTPException(status_code=400, detail="Aucune leçon associée à cette tâche — génère le cours avant de partager.")
    return job


@router.post("/lessons/{job_public_id}/share")
def enable_share(
    job_public_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    """Active le partage public (idempotent — réutilise le token existant)."""
    job = _owner_job_or_404(db, job_public_id, user)
    if not job.public_share_token:
        job.public_share_token = secrets.token_urlsafe(32)
        job.public_share_enabled_at = utc_now()
        db.add(job)
        db.commit()
        db.refresh(job)
    return {
        "token": job.public_share_token,
        "url": _share_url(job.public_share_token),
        "views": int(job.public_share_views or 0),
        "enabled_at": job.public_share_enabled_at.isoformat() if job.public_share_enabled_at else None,
    }


@router.delete("/lessons/{job_public_id}/share")
def disable_share(
    job_public_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    job = _owner_job_or_404(db, job_public_id, user)
    if job.public_share_token:
        job.public_share_token = None
        job.public_share_enabled_at = None
        db.add(job)
        db.commit()
    return {"ok": True}


@router.get("/lessons/{job_public_id}/share")
def share_status(
    job_public_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    """Renvoie l'état actuel — utile pour pré-remplir l'UI sans déclencher d'effet de bord."""
    job = _owner_job_or_404(db, job_public_id, user)
    return {
        "enabled": bool(job.public_share_token),
        "token": job.public_share_token,
        "url": _share_url(job.public_share_token) if job.public_share_token else None,
        "views": int(job.public_share_views or 0),
        "enabled_at": job.public_share_enabled_at.isoformat() if job.public_share_enabled_at else None,
    }


@router.get("/public/lesson/{token}")
def get_public_lesson(
    token: str,
    db: Annotated[Session, Depends(get_db)],
):
    """Route publique — pas d'auth requise. Ne renvoie aucune identité du propriétaire."""
    if not token or len(token) < 16 or len(token) > 128:
        raise HTTPException(status_code=404, detail="Lien invalide.")
    job = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.public_share_token == token)
        .one_or_none()
    )
    if job is None or not (job.lesson_markdown or "").strip():
        raise HTTPException(status_code=404, detail="Cette leçon n'existe pas ou n'est plus partagée.")

    # Compteur de vues : best-effort, on n'attache pas de transaction stricte ici.
    try:
        job.public_share_views = int(job.public_share_views or 0) + 1
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()

    # Tentative discrète de récupérer le code de parrainage du propriétaire pour le CTA viral.
    referral_code = None
    if job.user_id is not None:
        owner = db.get(User, job.user_id)
        if owner and owner.referral_code:
            referral_code = owner.referral_code

    return {
        "subject": job.subject or "General",
        "ui_locale": job.ui_locale or "fr",
        "speech_language": job.speech_language or "fr",
        "lesson_markdown": job.lesson_markdown,
        "generated_at": job.updated_at.isoformat() if job.updated_at else None,
        "views": int(job.public_share_views or 0),
        # Pour le bandeau CTA "Génère le tien" — incite à inscription via le code du partageur.
        "referrer_code": referral_code,
    }
