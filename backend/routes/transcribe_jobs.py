from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from credits_wallet import credit_credits, debit_credits
from database import SessionLocal, get_db
from deps import auth_required, require_wallet_user
from models import TranscriptionJob, User
from routes import transcribe as tr
from transcription_retail_catalog import get_retail_model

logger = logging.getLogger(__name__)

router = APIRouter(tags=["transcribe-jobs"])

_job_slot_sem: Optional[asyncio.Semaphore] = None
_job_slot_capacity: Optional[int] = None


def init_transcribe_job_slots() -> None:
    """
    À appeler une fois au démarrage (lifespan FastAPI).

    TRANSCRIBE_JOB_MAX_CONCURRENT (défaut 2) : nombre maximal de jobs /transcribe-jobs dont
    l’étape Whisper+FFmpeg peut s’exécuter à la fois **dans ce processus uvicorn**.

    0 ou « unlimited » → pas de limite (comme avant ; déconseillé en prod sous charge locale).

    Avec plusieurs workers uvicorn (``--workers N``), la capacité réelle ≈ ``N ×`` cette valeur.
    """
    global _job_slot_sem, _job_slot_capacity
    raw = (os.getenv("TRANSCRIBE_JOB_MAX_CONCURRENT") or "2").strip().lower()
    if raw in ("0", "unlimited", "none", "off"):
        _job_slot_sem = None
        _job_slot_capacity = None
        logger.info("TRANSCRIBE_JOB_MAX_CONCURRENT désactivée — aucune limite intra-process.")
        return
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "TRANSCRIBE_JOB_MAX_CONCURRENT invalide (%r), utilisation du défaut 2.",
            os.getenv("TRANSCRIBE_JOB_MAX_CONCURRENT"),
        )
        n = 2
    n = max(1, min(n, 64))
    _job_slot_sem = asyncio.Semaphore(n)
    _job_slot_capacity = n
    logger.info(
        "File jobs transcription — au plus %s job(s) lourd(s) en parallèle (ce process uvicorn).",
        n,
    )


def get_transcription_job_slot_capacity() -> Optional[int]:
    """Valeur entière configurée, ou ``None`` si illimitée. Exposée via /api/health."""
    return _job_slot_capacity


def reset_transcribe_job_slots_for_tests() -> None:
    """Réinitialise le sémaphore (tests uniquement)."""
    global _job_slot_sem, _job_slot_capacity
    _job_slot_sem = None
    _job_slot_capacity = None


def _coerce_dt_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def bootstrap_resume_transcription_jobs() -> None:
    """
    Au démarrage, relance les jobs encore `queued` et récupère les jobs `processing` orphelins.

    Important: Les jobs sont exécutés **dans le process uvicorn** (pas de worker externe).
    En cas de redémarrage, un job peut rester `processing` indéfiniment sans cette reprise.
    """
    stale_min = int(os.getenv("TRANSCRIBE_JOB_STALE_MINUTES", "20") or "20")
    resume_limit = int(os.getenv("TRANSCRIBE_BOOTSTRAP_RESUME_LIMIT", "20") or "20")
    stale_min = max(5, min(stale_min, 24 * 60))
    resume_limit = max(1, min(resume_limit, 200))

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        proc = db.scalars(select(TranscriptionJob).where(TranscriptionJob.status == "processing")).all()
        for job in proc:
            upd = _coerce_dt_utc(getattr(job, "updated_at", None))
            if upd is None or (now - upd) <= timedelta(minutes=stale_min):
                continue
            ui = tr._normalize_ui_locale(getattr(job, "ui_locale", None))
            
            # Rembourser la réserve avant de remettre en file d'attente pour éviter la double facturation
            if job.user_id is not None:
                try:
                    u_auth = db.get(User, job.user_id)
                    _release_job_wallet_hold(db, job, u_auth)
                except Exception:
                    pass
            
            job.status = "queued"
            job.phase = "requeued"
            job.progress_percent = 1
            job.status_message = tr._ui_text(
                ui,
                "Reprise serveur — tâche remise en file d’attente.",
                "استئناف الخادم — تمت إعادة المهمة إلى قائمة الانتظار.",
            )[:768]
            db.add(job)
        db.commit()

        queued = db.scalars(
            select(TranscriptionJob)
            .where(TranscriptionJob.status == "queued")
            .order_by(TranscriptionJob.created_at.asc())
            .limit(resume_limit)
        ).all()
        for job in queued:
            asyncio.create_task(execute_transcription_job(job.public_id))
    finally:
        db.close()


@asynccontextmanager
async def transcription_job_execution_slot() -> AsyncIterator[None]:
    """Limite globale intra-process avant marquage ``processing`` + travail Whisper."""
    sem = _job_slot_sem
    if sem is None:
        yield
        return
    await sem.acquire()
    try:
        yield
    finally:
        sem.release()

_DATA = Path(__file__).resolve().parent.parent / "data"


def _job_root(public_id: str) -> Path:
    return _DATA / "jobs" / public_id


def _detail_to_plain_str(detail: Any) -> str:
    if isinstance(detail, str):
        s = detail.strip()
        base = s if s else "Erreur inconnue."
    else:
        base = str(detail).strip() or "Erreur inconnue."
    return tr.sanitize_user_visible_transcription_detail(base)


def _purge_job_workspace(public_id: str) -> None:
    shutil.rmtree(_job_root(public_id), ignore_errors=True)


def _release_job_wallet_hold(db: Session, job: TranscriptionJob, auth_user: Optional[User]) -> None:
    """Recrédite la réserve si le job échoue / est interrompu avant facturation finale."""
    if auth_user is None or job is None:
        return
    try:
        reserved = int(getattr(job, "wallet_reserved_units", 0) or 0)
    except (TypeError, ValueError):
        reserved = 0
    if reserved <= 0:
        return
    credit_credits(db, auth_user, reserved)
    job.wallet_reserved_units = 0
    db.add(job)
    db.commit()


async def execute_transcription_job(job_public_id: str) -> None:
    async with transcription_job_execution_slot():
        await _execute_transcription_job_after_slot(job_public_id)


async def _execute_transcription_job_after_slot(job_public_id: str) -> None:
    whisper_rt = float(os.getenv("WHISPER_PROGRESS_RT_FACTOR", "0.5"))
    ctx: Optional[dict[str, Any]] = None
    db = SessionLocal()
    job: Optional[TranscriptionJob] = None
    claimed_success = False

    try:
        claimed = db.execute(
            update(TranscriptionJob)
            .where(TranscriptionJob.public_id == job_public_id, TranscriptionJob.status == "queued")
            .values(status="processing", phase="running", progress_percent=2),
        )
        db.commit()
        if claimed.rowcount == 0:
            return

        claimed_success = True
        job = db.scalars(select(TranscriptionJob).where(TranscriptionJob.public_id == job_public_id)).first()
        if job is None:
            logger.warning("TranscriptionJob introuvable après claim public_id=%s", job_public_id)
            return

        job_ui_loc = tr._normalize_ui_locale(getattr(job, "ui_locale", None))

        inp = (_DATA / job.input_relpath).resolve()
        if not inp.is_file():
            job.status = "failed"
            job.error_detail = "Fichier importé introuvable sur le serveur (stockage)."
            db.commit()
            return

        auth_user: Optional[User] = None
        if job.user_id is not None:
            auth_user = db.get(User, job.user_id)

        try:
            ctx = await tr._load_transcribe_context_from_path(
                str(inp),
                job.original_filename,
                job.subject or "General",
                job.speech_language or "fr",
                transcription_engine_in=getattr(job, "transcription_engine", None) or "openai",
                hint_content_type=job.client_content_type,
                user_credit_balance=auth_user.credit_balance if auth_user else 0,
            )
        except HTTPException as e:
            job.status = "failed"
            job.error_detail = _detail_to_plain_str(e.detail)
            job.progress_percent = 0
            db.commit()
            return

        ctx["wallet_reserve_units"] = 0
        if (
            auth_user is not None
            and auth_required()
            and tr._env_truthy("TRANSCRIBE_JOB_WALLET_HOLD", True)
        ):
            hold = tr.estimate_transcription_job_wallet_hold_units(
                estimated_duration_seconds=ctx.get("estimated"),
                transcription_engine=str(ctx.get("transcription_engine") or "openai"),
            )
            if hold > 0:
                try:
                    debit_credits(db, auth_user, hold)
                except HTTPException as e:
                    job.status = "failed"
                    job.error_detail = _detail_to_plain_str(e.detail)
                    job.progress_percent = 0
                    job.phase = None
                    db.commit()
                    return
                job.wallet_reserved_units = hold
                ctx["wallet_reserve_units"] = hold
                db.commit()

        if ctx.get("estimated") is not None:
            job.estimated_duration_seconds = float(ctx["estimated"])
        last_save_mono = time.monotonic()
        last_announced_pct = -1

        ctx["transcription_job_id"] = job.id

        async for ev in tr.iterate_transcription_events(
            ctx=ctx,
            db=db,
            _auth=auth_user,
            subject=job.subject or "General",
            display_filename=job.original_filename,
            whisper_rt=whisper_rt,
            ui_locale=job_ui_loc,
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
                _release_job_wallet_hold(db, job, auth_user)
                job.status = "failed"
                detail = ev.get("detail")
                job.error_detail = _detail_to_plain_str(detail)
                db.commit()
                break

            if typ == "done":
                payload = ev.get("result")
                if not isinstance(payload, dict):
                    _release_job_wallet_hold(db, job, auth_user)
                    job.status = "failed"
                    job.error_detail = "Réponse transcription vide après finalisation."
                    db.commit()
                    break
                try:
                    job.result_json = json.dumps(payload, ensure_ascii=False, default=str)
                except TypeError:
                    _release_job_wallet_hold(db, job, auth_user)
                    job.status = "failed"
                    job.error_detail = "Impossible de sérialiser la transcription finale."
                    db.commit()
                    break
                job.status = "done"
                job.phase = None
                job.progress_percent = 100
                job.status_message = tr._ui_text(job_ui_loc, "Terminé", "تم")
                job.wallet_reserved_units = 0
                db.commit()
                break

    except Exception:
        logger.exception("execute_transcription_job public_id=%s", job_public_id)
        try:
            if job is not None:
                auth_u = db.get(User, job.user_id) if job.user_id is not None else None
                _release_job_wallet_hold(db, job, auth_u)
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
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
    file: UploadFile = File(...),
    subject: str = Form(default="General"),
    speech_language: str = Form(default="fr"),
    transcription_engine: str = Form(default="openai"),
    ui_locale: str = Form(default="fr"),
):
    tr._reject_disallowed_media_type(file)

    engine_norm = tr._normalize_transcription_engine(transcription_engine)
    spec = get_retail_model(engine_norm)
    if spec.provider != "local":
        if spec.provider == "openai" and not (os.getenv("OPENAI_API_KEY") or "").strip():
            raise HTTPException(
                status_code=500,
                detail="La clé technique de transcription (OpenAI) est manquante sur le serveur.",
            )
        if spec.provider == "groq" and not (os.getenv("GROQ_API_KEY") or "").strip():
            raise HTTPException(
                status_code=500,
                detail="La clé Groq (GROQ_API_KEY) est manquante sur le serveur pour ce modèle.",
            )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in tr.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in tr.ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Extension non audio ou non supportée. Formats acceptés : {allowed}.",
        )

    public_id = uuid.uuid4().hex
    jdir = _job_root(public_id)
    jdir.mkdir(parents=True, exist_ok=True)
    fname_disk = f"upload{ext or '.bin'}"
    rel = Path("jobs") / public_id / fname_disk
    abs_path = _DATA / rel
    
    # Spooling par chunks (évite le DoS RAM)
    max_bytes = tr.MAX_SIZE_MB * 1024 * 1024
    size_bytes = 0
    try:
        with open(abs_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Fichier trop volumineux. Taille maximale : {tr.MAX_SIZE_MB} Mo "
                            "(paramètre TRANSCRIBE_MAX_MB sur le serveur)."
                        ),
                    )
                f.write(chunk)
    except Exception:
        _purge_job_workspace(public_id)
        raise

    uid: Optional[int] = None
    if auth_required():
        if _auth is None:
            raise HTTPException(status_code=401, detail="Connexion requise pour la transcription.")
        uid = _auth.id

    speech_lang = ("ar" if (speech_language or "").strip().lower().startswith("ar") else "fr")[:16]
    ui_loc = tr._normalize_ui_locale(ui_locale)[:16]
    queued_msg = tr._ui_text(
        ui_loc,
        "Import terminé — file d’attente serveur.",
        "اكتمل الاستيراد — في قائمة انتظار الخادم.",
    )
    job = TranscriptionJob(
        public_id=public_id,
        user_id=uid,
        original_filename=(file.filename or fname_disk)[:384],
        subject=(subject or "General")[:512],
        speech_language=speech_lang,
        ui_locale=ui_loc,
        transcription_engine=engine_norm[:24],
        input_relpath=str(rel.as_posix()),
        client_content_type=(file.content_type or "")[:160] or None,
        status="queued",
        progress_percent=1,
        phase="received",
        status_message=queued_msg[:768],
    )
    db.add(job)
    db.commit()

    # Lance l'exécution dans ce process. La reprise au démarrage gère les crash/restart.
    asyncio.create_task(execute_transcription_job(public_id))

    return JSONResponse({"job_id": public_id, "status": job.status}, status_code=202)


@router.post("/transcribe-jobs/{job_public_id}/cancel")
def cancel_transcription_job(
    job_public_id: str,
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
):
    """Annule uniquement un job encore « queued » (fichier supprimé, aucune facturation ni réserve)."""
    job = db.scalars(select(TranscriptionJob).where(TranscriptionJob.public_id == job_public_id)).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    if auth_required():
        if _auth is None:
            raise HTTPException(status_code=401, detail="Connexion requise.")
        if _auth.id != job.user_id:
            raise HTTPException(status_code=403, detail="Accès refusé.")
    elif job.user_id is not None:
        raise HTTPException(status_code=403, detail="Connexion requise.")
    if job.status != "queued":
        raise HTTPException(
            status_code=409,
            detail="Cette transcription a déjà commencé ou est terminée — annulation impossible.",
        )
    ui = tr._normalize_ui_locale(getattr(job, "ui_locale", None))
    job.status = "cancelled"
    job.progress_percent = 0
    job.phase = "cancelled"
    job.status_message = tr._ui_text(ui, "Annulé avant traitement.", "أُلغي قبل المعالجة.")[:768]
    job.error_detail = None
    db.commit()
    _purge_job_workspace(job_public_id)
    return JSONResponse({"ok": True, "status": "cancelled"})


def _serialize_job(job: TranscriptionJob, *, include_result: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job.public_id,
        "original_filename": job.original_filename,
        "subject": job.subject,
        "speech_language": job.speech_language,
        "transcription_engine": getattr(job, "transcription_engine", None) or "openai",
        "status": job.status,
        "progress_percent": job.progress_percent,
        "phase": job.phase,
        "message": job.status_message,
        "estimated_duration_seconds": job.estimated_duration_seconds,
        "lesson": job.lesson_markdown,
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
