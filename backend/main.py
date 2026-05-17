import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, SessionLocal, engine
from deps import auth_required
from models import (  # noqa: F401 — charge models + tables
    AppUserFeedback,
    ChatMessage,
    ChatThread,
    CreditTopUpRequest,
    ReferralEvent,
    TranscriptionJob,
    TranscriptionJobRating,
    User,
    UserNotification,
    UserTranscriptionModelHours,
)
from admin_sync import sync_designated_admin
from env_validation import validate_env
from rate_limit import install_rate_limiter
from routes import (
    admin_credits,
    admin_feedback,
    auth,
    chat,
    credits,
    export,
    feedback,
    generate,
    notifications,
    referrals as referrals_routes,
    share,
    transcript_insight,
    transcribe,
    transcribe_jobs,
    telegram as telegram_routes,
    whatsapp as whatsapp_routes,
)
from schema_migrate import (
    ensure_chat_schema,
    ensure_credit_schema,
    ensure_notification_schema,
    ensure_public_share_schema,
    ensure_referrals_schema,
    ensure_telegram_source_schema,
    ensure_transcription_jobs_schema,
    ensure_user_transcription_model_hours_schema,
    ensure_whatsapp_source_schema,
)
from security import jwt_secret

# Resolve .env next to this file so it works regardless of cwd. override=True ensures
# values from .env win over stale OPENAI_* / GROQ_* exported in the shell.
_env_file = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_file, override=True)

logger = logging.getLogger(__name__)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _data = Path(__file__).resolve().parent / "data"
    _data.mkdir(parents=True, exist_ok=True)
    validate_env(auth_required=auth_required())
    if auth_required():
        jwt_secret()
    Base.metadata.create_all(bind=engine)
    ensure_credit_schema(engine)
    ensure_user_transcription_model_hours_schema(engine)
    if _env_truthy("RESET_TRANSCRIPTION_LOYALTY_ON_STARTUP"):
        from transcription_loyalty import reset_all_transcription_loyalty_counters

        db = SessionLocal()
        try:
            stats = reset_all_transcription_loyalty_counters(db)
            logger.warning(
                "RESET_TRANSCRIPTION_LOYALTY_ON_STARTUP actif — compteurs fidélité transcription remis à zéro : %s",
                stats,
            )
        except Exception:
            logger.exception("RESET_TRANSCRIPTION_LOYALTY_ON_STARTUP : échec du reset")
            raise
        finally:
            db.close()
    ensure_transcription_jobs_schema(engine)
    ensure_chat_schema(engine)
    ensure_notification_schema(engine)
    ensure_public_share_schema(engine)
    ensure_whatsapp_source_schema(engine)
    ensure_telegram_source_schema(engine)
    ensure_referrals_schema(engine)
    sync_designated_admin()
    transcribe_jobs.init_transcribe_job_slots()
    await transcribe_jobs.bootstrap_resume_transcription_jobs()
    yield


app = FastAPI(title="LecturAI API", version="1.0.0", lifespan=lifespan)

# Rate-limiting global + handler 429. Limites fines posées via @limiter.limit() côté routes.
install_rate_limiter(app)

# CORS — domaines autorisés à appeler l'API depuis un navigateur.
# Production : lister explicitement les domaines (séparés par virgule). Jamais "*" si AUTH_REQUIRED=true,
# sinon n'importe quel site malveillant peut piloter l'API avec le JWT d'un user connecté (CSRF).
# Pour les preview deploys Vercel (URLs dynamiques par PR), utilise ALLOWED_ORIGIN_REGEX.
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX", "").strip() or None

if _raw_origins.strip() == "*":
    _origins = ["*"]
else:
    _origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# Garde-fou : credentials + wildcard est ignoré par les navigateurs ET dangereux. On bascule en
# liste vide + regex si l'admin a oublié de remplir ALLOWED_ORIGINS en prod, et on log fort.
if _origins == ["*"] and auth_required():
    logger.warning(
        "ALLOWED_ORIGINS=* avec AUTH_REQUIRED=true — configuration non sûre. "
        "Définis ALLOWED_ORIGINS=https://ton-domaine en environnement de production."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Accept-Language", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)

app.include_router(auth.router, prefix="/api")
app.include_router(credits.router, prefix="/api")
app.include_router(admin_credits.router, prefix="/api")
app.include_router(admin_feedback.router, prefix="/api")
app.include_router(transcribe.router, prefix="/api")
app.include_router(transcribe_jobs.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(transcript_insight.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(referrals_routes.router, prefix="/api")
app.include_router(share.router, prefix="/api")
app.include_router(whatsapp_routes.router, prefix="/api")
app.include_router(telegram_routes.router, prefix="/api")


@app.get("/")
def root():
    return {"status": "LecturAI API running", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "transcription_jobs_max_concurrent": transcribe_jobs.get_transcription_job_slot_capacity(),
    }
