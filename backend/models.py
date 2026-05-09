from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nni: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    whatsapp_phone: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    credit_balance: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    credits_expire_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false(), default=False)

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
    input_relpath: Mapped[str] = mapped_column(String(512), nullable=False)
    client_content_type: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True, server_default="queued")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status_message: Mapped[Optional[str]] = mapped_column(String(768), nullable=True)
    estimated_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
