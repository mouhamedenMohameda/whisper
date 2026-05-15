from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from credits_wallet import utc_now
from database import get_db
from deps import require_user
from models import User, UserNotification
from pricing import wallet_units_to_mru_display

router = APIRouter(tags=["notifications"])


def _serialize(n: UserNotification) -> dict:
    mru_value = n.mru_credited
    if mru_value is None and n.credits_granted is not None:
        mru_value = wallet_units_to_mru_display(int(n.credits_granted))
    return {
        "id": n.id,
        "kind": n.kind,
        "topup_request_id": n.topup_request_id,
        "credits_granted": n.credits_granted,
        "mru_credited": mru_value,
        "admin_note": n.admin_note,
        "read": n.read_at is not None,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/notifications")
def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(require_user),
):
    """Liste les notifications de l’utilisateur (les plus récentes en premier)."""
    if user is None:
        raise HTTPException(status_code=401, detail="Connexion requise.")

    total = int(
        db.scalar(
            select(func.count(UserNotification.id)).where(UserNotification.user_id == user.id)
        )
        or 0
    )
    unread = int(
        db.scalar(
            select(func.count(UserNotification.id)).where(
                UserNotification.user_id == user.id, UserNotification.read_at.is_(None)
            )
        )
        or 0
    )

    rows = (
        db.execute(
            select(UserNotification)
            .where(UserNotification.user_id == user.id)
            .order_by(UserNotification.created_at.desc(), UserNotification.id.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    return {
        "notifications": [_serialize(n) for n in rows],
        "total": total,
        "unread": unread,
        "limit": limit,
        "offset": offset,
    }


@router.get("/notifications/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(require_user),
):
    if user is None:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    unread = int(
        db.scalar(
            select(func.count(UserNotification.id)).where(
                UserNotification.user_id == user.id, UserNotification.read_at.is_(None)
            )
        )
        or 0
    )
    return {"unread": unread}


@router.post("/notifications/{notification_id}/read")
def mark_one_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(require_user),
):
    if user is None:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    row = db.get(UserNotification, notification_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification introuvable.")
    if row.read_at is None:
        row.read_at = utc_now()
        db.add(row)
        db.commit()
        db.refresh(row)
    return {"ok": True, "notification": _serialize(row)}


@router.post("/notifications/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(require_user),
):
    if user is None:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    now = utc_now()
    rows = (
        db.execute(
            select(UserNotification).where(
                UserNotification.user_id == user.id, UserNotification.read_at.is_(None)
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.read_at = now
        db.add(r)
    db.commit()
    return {"ok": True, "updated": len(rows)}
