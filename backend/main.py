import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine
from deps import auth_required
from models import CreditTopUpRequest, TranscriptionJob, User  # noqa: F401 — charge models + tables
from admin_sync import sync_designated_admin
from routes import admin_credits, auth, credits, export, generate, transcript_insight, transcribe, transcribe_jobs
from schema_migrate import ensure_credit_schema, ensure_transcription_jobs_schema
from security import jwt_secret

# Resolve .env next to this file so it works regardless of cwd. override=True ensures
# values from .env win over stale OPENAI_* / GROQ_* exported in the shell.
_env_file = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_file, override=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _data = Path(__file__).resolve().parent / "data"
    _data.mkdir(parents=True, exist_ok=True)
    if auth_required():
        jwt_secret()
    Base.metadata.create_all(bind=engine)
    ensure_credit_schema(engine)
    ensure_transcription_jobs_schema(engine)
    sync_designated_admin()
    transcribe_jobs.init_transcribe_job_slots()
    yield


app = FastAPI(title="LecturAI API", version="1.0.0", lifespan=lifespan)

_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
if _raw_origins.strip() == "*":
    _origins = ["*"]
else:
    _origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(credits.router, prefix="/api")
app.include_router(admin_credits.router, prefix="/api")
app.include_router(transcribe.router, prefix="/api")
app.include_router(transcribe_jobs.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(transcript_insight.router, prefix="/api")
app.include_router(export.router, prefix="/api")


@app.get("/")
def root():
    return {"status": "LecturAI API running", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "transcription_jobs_max_concurrent": transcribe_jobs.get_transcription_job_slot_capacity(),
    }
