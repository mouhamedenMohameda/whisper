from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    """Utilisateur applicatif.

    **Portefeuille (`credit_balance`)** — Le solde en base est un **entier d’unités** (pas de fraction
    stockée en colonne). Sémantique : unités ≈ ``MRU_affiché × MRU_WALLET_MICRO`` (voir ``pricing``).
    On ne peut pas représenter une fraction d’unité sans changer le schéma (ex. BIGINT + échelle,
    ou table d’écritures en sous-unités) ou sans **augmenter** ``MRU_WALLET_MICRO`` dans l’environnement
    pour réduire l’erreur de quantification. La conversion MRU → unités côté code utilise un arrondi
    **demi au plus proche** (compromis neutre : ni ``ceil`` systématique, ni ``floor`` biais client).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nni: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    whatsapp_phone: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    credit_balance: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        default=0,
        comment=(
            "Solde portefeuille en unités entières (pas de fraction en base). "
            "MRU affiché ≈ credit_balance / MRU_WALLET_MICRO ; quantification et arrondi : voir pricing.py."
        ),
    )
    credits_expire_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false(), default=False)

    hours_transcribed_lifetime: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default="0",
        default=0.0,
        comment="Somme des heures par modèle (dénormalisé ; paliers = user_transcription_model_hours).",
    )

    transcription_model_hours: Mapped[list["UserTranscriptionModelHours"]] = relationship(
        "UserTranscriptionModelHours",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    topup_requests: Mapped[list["CreditTopUpRequest"]] = relationship(
        "CreditTopUpRequest", back_populates="user", cascade="all, delete-orphan"
    )


class CreditTopUpRequest(Base):
    __tablename__ = "credit_top_up_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stored_filename: Mapped[str] = mapped_column(String(384), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="pending")
    credits_granted: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="topup_requests")


class UserTranscriptionModelHours(Base):
    """Heures cumulées par utilisateur et par id de modèle (palier fidélité indépendant par moteur)."""

    __tablename__ = "user_transcription_model_hours"
    __table_args__ = (UniqueConstraint("user_id", "model_id", name="uq_user_transcription_model_hours_user_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    hours_cumulative: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default="0",
        default=0.0,
    )

    user: Mapped["User"] = relationship("User", back_populates="transcription_model_hours")


class UserNotification(Base):
    """Notification adressée à un utilisateur (recharge validée, don admin, etc.).

    ``kind`` :
      - ``topup_approved`` : demande de recharge validée par l’admin
      - ``admin_grant``    : crédit ajouté manuellement par un admin (don gratuit)
    """

    __tablename__ = "user_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    topup_request_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("credit_top_up_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    credits_granted: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mru_credited: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    admin_note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    original_filename: Mapped[str] = mapped_column(String(384), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False, server_default="General")
    speech_language: Mapped[str] = mapped_column(String(16), nullable=False, server_default="fr")
    ui_locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="fr")
    transcription_engine: Mapped[str] = mapped_column(String(24), nullable=False, server_default="openai")
    input_relpath: Mapped[str] = mapped_column(String(512), nullable=False)
    client_content_type: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True, server_default="queued")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status_message: Mapped[Optional[str]] = mapped_column(String(768), nullable=True)
    estimated_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Réserve portefeuille (unités wallet) débitée au passage « queued → processing » si TRANSCRIBE_JOB_WALLET_HOLD ; libérée ou soldée au résultat.
    wallet_reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    # Heures (fractionnaires) déjà comptabilisées sur ``user_transcription_model_hours`` pour ce job ; NULL = pas encore appliqué.
    lifetime_hours_applied: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TranscriptionJobRating(Base):
    """Note optionnelle (1–5) après transcription — une ligne par tâche ``transcription_jobs``."""

    __tablename__ = "transcription_job_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transcription_job_id: Mapped[int] = mapped_column(
        ForeignKey("transcription_jobs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChatThread(Base):
    """Fil de discussion assistant (Groq) — résumé roulant + messages récents."""

    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, server_default="Discussion")
    rolling_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    summary_folded_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="thread", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Facturation / usage (renseignés surtout pour role="assistant")
    billed_mru: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    provider_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    debit_wallet_units: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    wallet_balance_units_after: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    thread: Mapped["ChatThread"] = relationship("ChatThread", back_populates="messages")


class AppUserFeedback(Base):
    """Retours globaux (idées, améliorations) saisis depuis l’app."""

    __tablename__ = "app_user_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    ui_locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="fr")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
