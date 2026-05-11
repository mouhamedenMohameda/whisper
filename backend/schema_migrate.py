"""Migrations légères (colonnes SQLite/Postgres absentes après ajout dans les modèles).

``users.credit_balance`` : entier d’unités portefeuille (pas de sous-unité en base). Voir
``models.User`` et ``pricing`` pour MRU affiché, ``MRU_WALLET_MICRO`` et arrondi demi au plus proche.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_credit_schema(engine: Engine) -> None:
    """Garantit les colonnes crédits sur ``users`` (solde entier, voir doc module)."""
    insp = inspect(engine)
    dialect = engine.dialect.name

    tables = insp.get_table_names()

    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        with engine.begin() as conn:
            if "credit_balance" not in cols:
                if dialect == "sqlite":
                    conn.execute(text("ALTER TABLE users ADD COLUMN credit_balance INTEGER NOT NULL DEFAULT 0"))
                else:
                    conn.execute(text("ALTER TABLE users ADD COLUMN credit_balance INTEGER NOT NULL DEFAULT 0"))
                cols.add("credit_balance")
            if "credits_expire_at" not in cols:
                if dialect == "sqlite":
                    conn.execute(text("ALTER TABLE users ADD COLUMN credits_expire_at TIMESTAMP"))
                elif dialect == "postgresql":
                    conn.execute(text("ALTER TABLE users ADD COLUMN credits_expire_at TIMESTAMPTZ"))
                else:
                    conn.execute(text("ALTER TABLE users ADD COLUMN credits_expire_at DATETIME"))
            if "is_admin" not in cols:
                if dialect == "sqlite":
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"))
                elif dialect == "postgresql":
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
                else:
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"))


def ensure_transcription_jobs_schema(engine: Engine) -> None:
    insp = inspect(engine)
    dialect = engine.dialect.name

    tables = insp.get_table_names()
    if "transcription_jobs" not in tables:
        return

    cols = {c["name"] for c in insp.get_columns("transcription_jobs")}
    with engine.begin() as conn:
        if "transcription_engine" not in cols:
            if dialect == "sqlite":
                conn.execute(
                    text("ALTER TABLE transcription_jobs ADD COLUMN transcription_engine VARCHAR(24) NOT NULL DEFAULT 'openai'"),
                )
            elif dialect == "postgresql":
                conn.execute(
                    text(
                        "ALTER TABLE transcription_jobs ADD COLUMN transcription_engine VARCHAR(24) "
                        "NOT NULL DEFAULT 'openai'",
                    ),
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE transcription_jobs ADD COLUMN transcription_engine VARCHAR(24) "
                        "NOT NULL DEFAULT 'openai'",
                    ),
                )

    cols = {c["name"] for c in insp.get_columns("transcription_jobs")}
    if "ui_locale" not in cols:
        with engine.begin() as conn:
            if dialect == "sqlite":
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN ui_locale VARCHAR(16) NOT NULL DEFAULT 'fr'"))
            elif dialect == "postgresql":
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN ui_locale VARCHAR(16) NOT NULL DEFAULT 'fr'"))
            else:
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN ui_locale VARCHAR(16) NOT NULL DEFAULT 'fr'"))

    cols = {c["name"] for c in insp.get_columns("transcription_jobs")}
    if "wallet_reserved_units" not in cols:
        with engine.begin() as conn:
            if dialect == "sqlite":
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN wallet_reserved_units INTEGER NOT NULL DEFAULT 0"))
            elif dialect == "postgresql":
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN wallet_reserved_units INTEGER NOT NULL DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN wallet_reserved_units INTEGER NOT NULL DEFAULT 0"))
