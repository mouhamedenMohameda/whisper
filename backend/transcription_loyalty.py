"""Compteurs d’heures transcrites par utilisateur **et par modèle** (paliers fidélité indépendants)."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from models import TranscriptionJob, User, UserTranscriptionModelHours
from transcription_retail_catalog import RETAIL_MODELS, canonical_transcription_model_id

logger = logging.getLogger(__name__)


def model_hours_for_user(db: Session, user_id: int, model_id: str) -> float:
    mid = canonical_transcription_model_id(model_id)
    row = db.scalars(
        select(UserTranscriptionModelHours).where(
            UserTranscriptionModelHours.user_id == int(user_id),
            UserTranscriptionModelHours.model_id == mid,
        ),
    ).first()
    if row is None:
        return 0.0
    return max(0.0, float(row.hours_cumulative or 0.0))


def all_model_hours_for_user(db: Session, user_id: int) -> dict[str, float]:
    """Toutes les entrées du catalogue avec heures (0 si jamais utilisé)."""
    out: dict[str, float] = {mid: 0.0 for mid in RETAIL_MODELS}
    rows = db.scalars(
        select(UserTranscriptionModelHours).where(UserTranscriptionModelHours.user_id == int(user_id)),
    ).all()
    for r in rows:
        mid = canonical_transcription_model_id(r.model_id)
        if mid in out:
            out[mid] = max(0.0, float(r.hours_cumulative or 0.0))
    return out


def _sync_user_total_hours(db: Session, user_id: int) -> None:
    """Met ``users.hours_transcribed_lifetime`` à la somme des lignes par modèle."""
    u = db.get(User, int(user_id))
    if u is None:
        return
    total = db.scalar(
        select(func.coalesce(func.sum(UserTranscriptionModelHours.hours_cumulative), 0.0)).where(
            UserTranscriptionModelHours.user_id == int(user_id),
        ),
    )
    u.hours_transcribed_lifetime = float(total or 0.0)
    db.add(u)


def apply_transcription_lifetime_hours(
    db: Session,
    *,
    user_id: int,
    model_id: str,
    duration_seconds: float,
    job_db_id: Optional[int],
) -> bool:
    """
    Incrémente les heures pour ``model_id`` uniquement (idempotence par job via
    ``TranscriptionJob.lifetime_hours_applied``). Sans ``job_db_id``, incrémente toujours
    (ex. ``/transcribe`` synchrone) — pas d’idempotence côté serveur.
    """
    delta_h = max(0.0, float(duration_seconds)) / 3600.0
    if delta_h <= 0:
        return False

    mid = canonical_transcription_model_id(model_id)
    if mid not in RETAIL_MODELS:
        logger.warning("apply_lifetime: modèle inconnu %r", model_id)
        return False

    if job_db_id is not None:
        job = db.get(TranscriptionJob, int(job_db_id))
        if job is None or job.user_id != user_id:
            logger.warning("apply_lifetime: job %s introuvable ou user mismatch", job_db_id)
            return False
        if job.lifetime_hours_applied is not None:
            return False
        job.lifetime_hours_applied = delta_h
        db.add(job)

    row = db.scalars(
        select(UserTranscriptionModelHours).where(
            UserTranscriptionModelHours.user_id == int(user_id),
            UserTranscriptionModelHours.model_id == mid,
        ),
    ).first()
    if row is None:
        db.add(
            UserTranscriptionModelHours(
                user_id=int(user_id),
                model_id=mid,
                hours_cumulative=float(delta_h),
            ),
        )
    else:
        row.hours_cumulative = float(row.hours_cumulative or 0.0) + float(delta_h)
        db.add(row)

    _sync_user_total_hours(db, user_id)
    db.commit()
    return True


def backfill_user_transcription_model_hours_from_legacy(db: Session) -> None:
    """
    Remplit ``user_transcription_model_hours`` à partir des jobs ``done`` puis réconcilie
    l’écart avec ``users.hours_transcribed_lifetime`` (excédent attribué à ``whisper-1``).
    À n’appeler que lorsque la table vient d’être créée et est vide.
    """
    legacy_totals: dict[int, float] = {}
    for u in db.scalars(select(User)).all():
        legacy_totals[int(u.id)] = float(getattr(u, "hours_transcribed_lifetime", 0) or 0.0)

    acc: defaultdict[tuple[int, str], float] = defaultdict(float)
    jobs = db.scalars(
        select(TranscriptionJob).where(TranscriptionJob.status == "done", TranscriptionJob.user_id.is_not(None)),
    ).all()
    for job in jobs:
        uid = int(job.user_id)  # type: ignore[arg-type]
        mid = canonical_transcription_model_id(job.transcription_engine)
        if mid not in RETAIL_MODELS:
            continue
        dur_sec = 0.0
        if job.result_json:
            try:
                payload = json.loads(job.result_json)
                ds = payload.get("duration_seconds")
                if isinstance(ds, (int, float)):
                    dur_sec = float(ds)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if dur_sec <= 0 and job.estimated_duration_seconds:
            try:
                dur_sec = float(job.estimated_duration_seconds or 0.0)
            except (TypeError, ValueError):
                dur_sec = 0.0
        if dur_sec <= 0:
            continue
        acc[(uid, mid)] += dur_sec / 3600.0

    for (uid, mid), h in acc.items():
        db.add(UserTranscriptionModelHours(user_id=uid, model_id=mid, hours_cumulative=float(h)))

    db.flush()

    for uid, legacy in legacy_totals.items():
        s = db.scalar(
            select(func.coalesce(func.sum(UserTranscriptionModelHours.hours_cumulative), 0.0)).where(
                UserTranscriptionModelHours.user_id == uid,
            ),
        )
        s_f = float(s or 0.0)
        gap = max(0.0, legacy - s_f)
        if gap <= 1e-9:
            continue
        canon = canonical_transcription_model_id("whisper-1")
        row = db.scalars(
            select(UserTranscriptionModelHours).where(
                UserTranscriptionModelHours.user_id == uid,
                UserTranscriptionModelHours.model_id == canon,
            ),
        ).first()
        if row is None:
            db.add(UserTranscriptionModelHours(user_id=uid, model_id=canon, hours_cumulative=gap))
        else:
            row.hours_cumulative = float(row.hours_cumulative or 0.0) + gap
            db.add(row)

    for uid in legacy_totals:
        _sync_user_total_hours(db, uid)

    db.commit()
    logger.info("Backfill user_transcription_model_hours terminé (%s jobs analysés).", len(jobs))


def reset_all_transcription_loyalty_counters(db: Session) -> dict[str, int]:
    """Remet à 0 tous les cumuls par modèle et ``users.hours_transcribed_lifetime`` pour chaque utilisateur.

    ``transcription_jobs.lifetime_hours_applied`` est laissé inchangé pour conserver l’idempotence
    (un job déjà compté ne ré-incrémente pas les heures).
    """
    r_del = db.execute(delete(UserTranscriptionModelHours))
    n_del = r_del.rowcount
    if n_del is None or n_del < 0:
        n_del = 0

    r_up = db.execute(update(User).values(hours_transcribed_lifetime=0.0))
    n_users = r_up.rowcount
    if n_users is None or n_users < 0:
        n_users = 0

    db.commit()
    from schema_migrate import mark_umh_legacy_backfill_done

    mark_umh_legacy_backfill_done(db.get_bind())
    return {"user_transcription_model_hours_rows_deleted": int(n_del), "users_hours_reset": int(n_users)}
