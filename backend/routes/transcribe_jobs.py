from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from deps import auth_required, require_wallet_user
from models import TranscriptionJob, User
from routes import transcribe as tr

logger = logging.getLogger(__name__)

router = APIRouter(tags=["transcribe-jobs"])

_DATA = Path(__file__).resolve().parent.parent / "data"


def _job_root(public_id: str) -> Path:
    return _DATA / "jobs" / public_id


def _detail_to_plain_str(detail: Any) -> str:
    if isinstance(detail, str):
        s = detail.strip()
        return s if s else "Erreur inconnue."
    return str(detail)


def _purge_job_workspace(public_id: str) -> None:
    shutil.rmtree(_job_root(public_id), ignore_errors=True)


async def execute_transcription_job(job_public_id: str) -> None:
    whisper_rt = float(os.getenv("WHISPER_PROGRESS_RT_FACTOR", "0.5"))
    ctx: Optional[dict[str, Any]] = None
    db = SessionLocal()
    job: Optional[TranscriptionJob] = None
    claimed_success = False

    try:
        claimed = db.execute(
            update(TranscriptionJob)
            .where(TranscriptionJob.public_id == job_public_id, TranscriptionJob.status == "queued")
            .values(status="processing"),
        )
        db.commit()
        if claimed.rowcount == 0:
            return

        claimed_success = True
        job = db.scalars(select(TranscriptionJob).where(TranscriptionJob.public_id == job_public_id)).first()
        if job is None:
            logger.warning("TranscriptionJob introuvable après claim public_id=%s", job_public_id)
            return

        inp = (_DATA / job.input_relpath).resolve()
        if not inp.is_file():
            job.status = "failed"
            job.error_detail = "Fichier importé introuvable sur le serveur (stockage)."
            db.commit()
            return

        try:
            ctx = tr._load_transcribe_context_from_path(
                str(inp),
                job.original_filename,
                job.subject or "General",
                job.speech_language or "fr",
                hint_content_type=job.client_content_type,
            )
        except HTTPException as e:
            job.status = "failed"
            job.error_detail = _detail_to_plain_str(e.detail)
            job.progress_percent = 0
            db.commit()
            return

        auth_user: Optional[User] = None
        if job.user_id is not None:
            auth_user = db.get(User, job.user_id)

        if ctx.get("estimated") is not None:
            job.estimated_duration_seconds = float(ctx["estimated"])
        last_save_mono = time.monotonic()
        last_announced_pct = -1

        async for ev in tr.iterate_transcription_events(
            ctx=ctx,
            db=db,
            _auth=auth_user,
            subject=job.subject or "General",
            display_filename=job.original_filename,
            whisper_rt=whisper_rt,
        ):
            typ = ev.get("type")
            phase = ev.get("phase") if isinstance(ev.get("phase"), str) else None
            msg = ev.get("message") if isinstance(ev.get("message"), str) else None
            srv = ev.get("server_frac")

            pct = job.progress_percent
            if isinstance(srv, (int, float)):
                pct = max(0, min(100, int(round(float(srv) * 100))))

            now_m = time.monotonic()
            if typ in ("status", "preview"):
                elapsed = now_m - last_save_mono
                if (
                    pct != last_announced_pct
                    or (phase is not None and phase != job.phase)
                    or (msg is not None and msg != job.status_message)
                ) and (elapsed > 0.48 or pct - last_announced_pct >= 3 or pct in (100,)):
                    job.progress_percent = pct
                    if phase:
                        job.phase = phase[:64]
                    if msg:
                        job.status_message = msg[:768]
                    db.commit()
                    last_save_mono = now_m
                    last_announced_pct = pct

            if typ == "error":
                job.status = "failed"
                detail = ev.get("detail")
                job.error_detail = _detail_to_plain_str(detail)
                db.commit()
                break

            if typ == "done":
                payload = ev.get("result")
                if not isinstance(payload, dict):
                    job.status = "failed"
                    job.error_detail = "Réponse transcription vide après finalisation."
                    db.commit()
                    break
                try:
                    job.result_json = json.dumps(payload, ensure_ascii=False, default=str)
                except TypeError:
                    job.status = "failed"
                    job.error_detail = "Impossible de sérialiser la transcription finale."
                    db.commit()
                    break
                job.status = "done"
                job.phase = None
                job.progress_percent = 100
                job.status_message = "Terminé"
                db.commit()
                break

    except Exception:
        logger.exception("execute_transcription_job public_id=%s", job_public_id)
        try:
            if job is not None:
                job.status = "failed"
                job.error_detail = "Erreur interne pendant la transcription. Réessaie plus tard."
                db.commit()
        except Exception:
            logger.exception("Impossible de marquer échec job public_id=%s", job_public_id)
    finally:
        if ctx is not None:
            tr._cleanup_transcription_tempfiles(
                ctx.get("tmp_path"),
                ctx.get("processed_mp3"),
                ctx.get("chunk_temp_paths"),
                preserve_tmp_path=True,
            )
        if claimed_success:
            _purge_job_workspace(job_public_id)
        db.close()


@router.post("/transcribe-jobs")
async def create_transcription_job(
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
    file: UploadFile = File(...),
    subject: str = Form(default="General"),
    speech_language: str = Form(default="fr"),
):
    tr._reject_disallowed_media_type(file)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="La clé technique de transcription est manquante sur le serveur.")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in tr.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in tr.ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Extension non audio ou non supportée. Formats acceptés : {allowed}.",
        )

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > tr.MAX_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Fichier trop volumineux ({size_mb:.1f} Mo). Taille maximale : {tr.MAX_SIZE_MB} Mo "
                "(paramètre TRANSCRIBE_MAX_MB sur le serveur)."
            ),
        )

    public_id = uuid.uuid4().hex
    jdir = _job_root(public_id)
    jdir.mkdir(parents=True, exist_ok=True)
    fname_disk = f"upload{ext or '.bin'}"
    rel = Path("jobs") / public_id / fname_disk
    abs_path = _DATA / rel
    abs_path.write_bytes(content)

    uid: Optional[int] = None
    if auth_required():
        if _auth is None:
            raise HTTPException(status_code=401, detail="Connexion requise pour la transcription.")
        uid = _auth.id

    job = TranscriptionJob(
        public_id=public_id,
        user_id=uid,
        original_filename=(file.filename or fname_disk)[:384],
        subject=(subject or "General")[:512],
        speech_language=("ar" if (speech_language or "").strip().lower().startswith("ar") else "fr")[:16],
        input_relpath=str(rel.as_posix()),
        client_content_type=(file.content_type or "")[:160] or None,
        status="queued",
        progress_percent=1,
        phase="received",
        status_message="Import terminé — file d’attente serveur.",
    )
    db.add(job)
    db.commit()

    background_tasks.add_task(execute_transcription_job, public_id)

    return JSONResponse({"job_id": public_id, "status": job.status}, status_code=202)


def _serialize_job(job: TranscriptionJob, *, include_result: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job.public_id,
        "original_filename": job.original_filename,
        "subject": job.subject,
        "speech_language": job.speech_language,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "phase": job.phase,
        "message": job.status_message,
        "estimated_duration_seconds": job.estimated_duration_seconds,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
    if job.error_detail:
        out["error_detail"] = job.error_detail
    if include_result and job.result_json:
        try:
            out["result"] = json.loads(job.result_json)
        except json.JSONDecodeError:
            out["result"] = None
    return out


@router.get("/transcribe-jobs/{job_public_id}")
def get_transcription_job(
    job_public_id: str,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
    include_result: bool = Query(default=False),
):
    job = db.scalars(select(TranscriptionJob).where(TranscriptionJob.public_id == job_public_id)).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Tâche de transcription introuvable.")

    if auth_required():
        if _auth is None:
            raise HTTPException(status_code=401, detail="Connexion requise.")
        if _auth.id != job.user_id:
            raise HTTPException(status_code=403, detail="Accès à cette tâche refusé.")
    elif job.user_id is not None:
        raise HTTPException(
            status_code=403,
            detail="Connexion requise pour consulter les tâches liées à un compte.",
        )

    return _serialize_job(job, include_result=include_result)


@router.get("/transcribe-jobs")
def list_transcription_jobs(
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
    limit: int = Query(default=40, ge=1, le=120),
):
    stmt = select(TranscriptionJob).order_by(TranscriptionJob.created_at.desc()).limit(limit)
    if auth_required():
        if _auth is None:
            raise HTTPException(status_code=401, detail="Connexion requise.")
        stmt = stmt.where(TranscriptionJob.user_id == _auth.id)
    else:
        stmt = stmt.where(TranscriptionJob.user_id.is_(None))

    rows = db.scalars(stmt).all()
    return {"items": [_serialize_job(j, include_result=False) for j in rows]}
