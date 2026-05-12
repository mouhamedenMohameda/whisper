"""Migrations légères (colonnes SQLite/Postgres absentes après ajout dans les modèles).

``users.credit_balance`` : entier d’unités portefeuille (pas de sous-unité en base). Voir
``models.User`` et ``pricing`` pour MRU affiché, ``MRU_WALLET_MICRO`` et arrondi demi au plus proche.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Empêche de relancer le backfill legacy après un reset admin (table ``user_transcription_model_hours`` vide).
UMH_LEGACY_BACKFILL_FLAG = "umh_legacy_backfill_v1"


def _ensure_lecturai_migration_flags_table(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS lecturai_migration_flags (
                name VARCHAR(64) PRIMARY KEY NOT NULL
            )
            """
        ),
    )


def _insert_umh_legacy_backfill_flag(conn, dialect: str) -> None:
    _ensure_lecturai_migration_flags_table(conn)
    if dialect == "sqlite":
        conn.execute(
            text("INSERT OR IGNORE INTO lecturai_migration_flags (name) VALUES (:n)"),
            {"n": UMH_LEGACY_BACKFILL_FLAG},
        )
    else:
        conn.execute(
            text(
                "INSERT INTO lecturai_migration_flags (name) VALUES (:n) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"n": UMH_LEGACY_BACKFILL_FLAG},
        )


def mark_umh_legacy_backfill_done(engine: Engine) -> None:
    """Marque le backfill legacy comme effectué (évite un re-backfill au prochain démarrage si la table est vide)."""
    dialect = engine.dialect.name
    with engine.begin() as conn:
        _insert_umh_legacy_backfill_flag(conn, dialect)


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
            if "hours_transcribed_lifetime" not in cols:
                if dialect == "postgresql":
                    conn.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN hours_transcribed_lifetime DOUBLE PRECISION NOT NULL DEFAULT 0",
                        ),
                    )
                else:
                    conn.execute(text("ALTER TABLE users ADD COLUMN hours_transcribed_lifetime FLOAT NOT NULL DEFAULT 0"))


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

    cols = {c["name"] for c in insp.get_columns("transcription_jobs")}
    if "lifetime_hours_applied" not in cols:
        with engine.begin() as conn:
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN lifetime_hours_applied DOUBLE PRECISION"))
            else:
                conn.execute(text("ALTER TABLE transcription_jobs ADD COLUMN lifetime_hours_applied FLOAT"))


def ensure_notification_schema(engine: Engine) -> None:
    """Crée la table ``user_notifications`` si elle n’existe pas (déploiement avant cette feature)."""
    insp = inspect(engine)
    dialect = engine.dialect.name
    tables = insp.get_table_names()
    if "user_notifications" in tables:
        return
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(
                text(
                    """
                    CREATE TABLE user_notifications (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        kind VARCHAR(32) NOT NULL,
                        topup_request_id INTEGER REFERENCES credit_top_up_requests(id) ON DELETE SET NULL,
                        credits_granted INTEGER,
                        mru_credited DOUBLE PRECISION,
                        admin_note VARCHAR(512),
                        read_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_notifications_user_id ON user_notifications(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_notifications_kind ON user_notifications(kind)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_notifications_topup_request_id ON user_notifications(topup_request_id)"))
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE user_notifications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        kind VARCHAR(32) NOT NULL,
                        topup_request_id INTEGER REFERENCES credit_top_up_requests(id) ON DELETE SET NULL,
                        credits_granted INTEGER,
                        mru_credited FLOAT,
                        admin_note VARCHAR(512),
                        read_at TIMESTAMP,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_notifications_user_id ON user_notifications(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_notifications_kind ON user_notifications(kind)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_notifications_topup_request_id ON user_notifications(topup_request_id)"))


def ensure_chat_schema(engine: Engine) -> None:
    """Garantit les colonnes usage/facturation sur `chat_messages`."""
    insp = inspect(engine)
    dialect = engine.dialect.name
    tables = insp.get_table_names()
    if "chat_messages" not in tables:
        return

    cols = {c["name"] for c in insp.get_columns("chat_messages")}
    with engine.begin() as conn:
        if "billed_mru" not in cols:
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN billed_mru DOUBLE PRECISION"))
            else:
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN billed_mru FLOAT"))
            cols.add("billed_mru")
        if "provider_usd" not in cols:
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN provider_usd DOUBLE PRECISION"))
            else:
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN provider_usd FLOAT"))
            cols.add("provider_usd")
        if "prompt_tokens" not in cols:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN prompt_tokens INTEGER"))
            cols.add("prompt_tokens")
        if "completion_tokens" not in cols:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN completion_tokens INTEGER"))
            cols.add("completion_tokens")
        if "debit_wallet_units" not in cols:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN debit_wallet_units INTEGER"))
            cols.add("debit_wallet_units")
        if "wallet_balance_units_after" not in cols:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN wallet_balance_units_after INTEGER"))
            cols.add("wallet_balance_units_after")


def ensure_user_transcription_model_hours_schema(engine: Engine) -> None:
    """Crée ``user_transcription_model_hours`` si besoin ; backfill legacy au plus une fois (drapeau SQL)."""
    insp = inspect(engine)
    dialect = engine.dialect.name
    tables = insp.get_table_names()
    if "user_transcription_model_hours" not in tables:
        with engine.begin() as conn:
            if dialect == "postgresql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE user_transcription_model_hours (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            model_id VARCHAR(24) NOT NULL,
                            hours_cumulative DOUBLE PRECISION NOT NULL DEFAULT 0,
                            CONSTRAINT uq_user_transcription_model_hours_user_model UNIQUE (user_id, model_id)
                        )
                        """
                    ),
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_user_transcription_model_hours_user_id "
                        "ON user_transcription_model_hours(user_id)",
                    ),
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_user_transcription_model_hours_model_id "
                        "ON user_transcription_model_hours(model_id)",
                    ),
                )
            else:
                conn.execute(
                    text(
                        """
                        CREATE TABLE user_transcription_model_hours (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            model_id VARCHAR(24) NOT NULL,
                            hours_cumulative FLOAT NOT NULL DEFAULT 0,
                            UNIQUE(user_id, model_id)
                        )
                        """
                    ),
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_user_transcription_model_hours_user_id "
                        "ON user_transcription_model_hours(user_id)",
                    ),
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_user_transcription_model_hours_model_id "
                        "ON user_transcription_model_hours(model_id)",
                    ),
                )

    with engine.begin() as conn:
        _ensure_lecturai_migration_flags_table(conn)
        backfill_done = (
            conn.execute(
                text("SELECT 1 FROM lecturai_migration_flags WHERE name = :n"),
                {"n": UMH_LEGACY_BACKFILL_FLAG},
            ).scalar()
            is not None
        )
        n = int(conn.execute(text("SELECT COUNT(*) FROM user_transcription_model_hours")).scalar() or 0)

    if backfill_done:
        return

    if n > 0:
        with engine.begin() as conn:
            _insert_umh_legacy_backfill_flag(conn, dialect)
        return

    from database import SessionLocal
    from transcription_loyalty import backfill_user_transcription_model_hours_from_legacy

    db = SessionLocal()
    try:
        backfill_user_transcription_model_hours_from_legacy(db)
        with engine.begin() as conn:
            _insert_umh_legacy_backfill_flag(conn, dialect)
    except Exception:
        logger.exception("Backfill user_transcription_model_hours a échoué — table vide ou partielle.")
        db.rollback()
    finally:
        db.close()
