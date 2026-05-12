from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_admin_user
from models import AppUserFeedback, User

router = APIRouter(tags=["admin"])


def _clean_q(raw: str) -> str:
    needle = "".join(ch for ch in (raw or "").strip().lower() if ch.isprintable()).strip()
    if len(needle) > 160:
        needle = needle[:160]
    return needle


@router.get("/admin/feedback/suggestions")
def admin_list_feedback_suggestions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str = "",
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_user),
):
    """Liste paginée des retours globaux (idées) envoyés depuis l’app."""
    needle = _clean_q(q)

    # Joins + filtre : soit dans le message, soit dans l'e-mail.
    join_stmt = select(AppUserFeedback, User.email).select_from(AppUserFeedback).outerjoin(
        User, User.id == AppUserFeedback.user_id
    )

    filters = []
    if len(needle) >= 2:
        filters.append(func.instr(func.lower(AppUserFeedback.message), needle) > 0)
        filters.append(func.instr(func.lower(func.coalesce(User.email, "")), needle) > 0)

    if filters:
        join_stmt = join_stmt.where(or_(*filters))

    count_stmt = select(func.count(AppUserFeedback.id)).select_from(AppUserFeedback).outerjoin(
        User, User.id == AppUserFeedback.user_id
    )
    if filters:
        count_stmt = count_stmt.where(or_(*filters))
    total = int(db.scalar(count_stmt) or 0)

    rows = (
        db.execute(
            join_stmt.order_by(AppUserFeedback.created_at.desc())
            .offset(offset)
            .limit(limit),
        )
        .all()
    )

    def pack(row: tuple[AppUserFeedback, Optional[str]]):
        fb, email = row
        return {
            "id": fb.id,
            "user_id": fb.user_id,
            "user_email": email or "",
            "ui_locale": fb.ui_locale,
            "message": fb.message,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        }

    return {"items": [pack(r) for r in rows], "total": total, "limit": limit, "offset": offset}

