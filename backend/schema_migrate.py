"""Migrations légères (colonnes SQLite/Postgres absentes après ajout dans les modèles)."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_credit_schema(engine: Engine) -> None:
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
