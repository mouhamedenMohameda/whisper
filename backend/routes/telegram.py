"""Webhook Bot Telegram.

Trois endpoints :
  - ``POST /api/telegram/webhook`` : Telegram pousse les updates. Auth via header
    ``X-Telegram-Bot-Api-Secret-Token`` qui doit matcher ``TELEGRAM_WEBHOOK_SECRET``.
  - ``GET  /api/telegram/health``  : ping public (sans secret) pour vérifier que le service répond.
  - ``POST /api/telegram/_simulate`` : route privée (header ``X-Lecturai-Sim``) pour tests locaux.

Contraintes Telegram :
  - Réponse < ~60s sinon Telegram retente (moins agressif que les 5s Meta — confortable).
  - On parse → on enqueue background → on ACK 200 immédiatement.
  - Doublons : Telegram peut retenter ; on dédoublonne sur ``telegram_message_id`` (update_id) côté processor.

Comment enregistrer le webhook côté Telegram (à faire une fois après déploiement) :

    curl -F "url=https://ai-whisper.radar-mr.com/api/telegram/webhook" \\
         -F "secret_token=$TELEGRAM_WEBHOOK_SECRET" \\
         -F "allowed_updates=[\"message\",\"callback_query\"]" \\
         https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from deps import require_user
from models import User
from telegram import config as tg_config
from telegram.parser import iter_inbound_messages
from telegram.processor import handle_inbound
from telegram.secret_token import verify as verify_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["telegram"])


# Durée de vie d'un token de liaison. 5 min suffit largement (l'user clique "Start" dans la
# seconde qui suit). Court = limite la fenêtre d'exploitation d'un éventuel leak réseau.
_LINK_TOKEN_TTL_SECONDS = 300


def _hash_token(token: str) -> str:
    """SHA-256 hex du token. On ne stocke jamais le token brut en DB."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bot_username() -> str:
    """Username du bot (sans @) — pour construire l'URL ``t.me/<bot>?start=<token>``.

    Lu depuis ``TELEGRAM_BOT_USERNAME`` (à poser dans `.env`). Défaut ``lecturai_bot``.
    """
    return (os.getenv("TELEGRAM_BOT_USERNAME") or "lecturai_bot").strip().lstrip("@")


@router.post("/telegram/link-token")
def issue_link_token(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(require_user),
) -> dict:
    """Génère un token de liaison single-use pour l'user authentifié.

    Retour : ``{deep_link, expires_at, already_linked, chat_id?}``.

    Sécurité — pourquoi ce flow vs ``/lier <numéro>`` côté bot :
      - Sans vérification, n'importe qui peut lier le numéro WhatsApp d'un autre user à son
        propre chat Telegram et drainer ses crédits.
      - Le token n'est jamais transmis par l'user : il transite app → URL Telegram → bot via
        ``/start <token>``. L'user authentifié dans l'app est forcément le propriétaire.
      - Stocké en DB sous forme de sha256(token) — un dump DB ne révèle pas le token.
      - Single-use : effacé à la consommation côté bot. TTL court (5 min).
    """
    if user is None:
        raise HTTPException(status_code=401, detail="Authentification requise.")

    if user.telegram_chat_id:
        # Déjà lié — on renvoie l'état sans régénérer de token (rien à faire côté user).
        return {
            "deep_link": None,
            "expires_at": None,
            "already_linked": True,
            "chat_id": user.telegram_chat_id,
        }

    # Token brut : 32 octets URL-safe → ~43 chars sans padding. Tient dans la limite Telegram
    # de 64 chars pour le paramètre ``start`` (https://core.telegram.org/bots#deep-linking).
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_LINK_TOKEN_TTL_SECONDS)

    user.telegram_link_token_hash = token_hash
    user.telegram_link_token_expires_at = expires_at
    db.commit()

    deep_link = f"https://t.me/{_bot_username()}?start={raw_token}"
    return {
        "deep_link": deep_link,
        "expires_at": expires_at.isoformat(),
        "already_linked": False,
    }


@router.post("/telegram/unlink")
def unlink_account(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(require_user),
) -> dict:
    """Délie le chat Telegram du compte de l'user authentifié.

    Cas d'usage : user a changé de téléphone / compte Telegram, veut re-lier autre chose.
    """
    if user is None:
        raise HTTPException(status_code=401, detail="Authentification requise.")
    was_linked = bool(user.telegram_chat_id)
    user.telegram_chat_id = None
    user.telegram_link_token_hash = None
    user.telegram_link_token_expires_at = None
    db.commit()
    return {"unlinked": was_linked}


@router.get("/telegram/health")
def health() -> dict:
    """Endpoint public sans secret — utile pour vérifier que le service tourne après deploy.

    Pas de fuite d'info sensible : on dit juste si les credentials d'envoi sont posés.
    """
    return {
        "ok": True,
        "credentials_configured": tg_config.has_credentials(),
        "webhook_secret_configured": tg_config.has_inbound_security(),
        "dry_run": tg_config.is_dry_run(),
    }


@router.post("/telegram/webhook")
async def receive_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: Annotated[str, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = "",
):
    """Réception d'updates Telegram. ACK rapide + background pour traiter."""
    if not verify_secret(x_telegram_bot_api_secret_token):
        logger.warning("Telegram webhook : secret_token invalide — update ignoré.")
        # On renvoie 200 sans signaler à l'attaquant qu'il y a eu un rejet.
        return JSONResponse({"received": True})

    try:
        payload = await request.json()
    except Exception:
        logger.warning("Telegram webhook : JSON invalide.")
        return JSONResponse({"received": True})

    # Si les credentials d'envoi ne sont pas posés, on ne ferait que des erreurs en background.
    if not tg_config.has_credentials() and not tg_config.is_dry_run():
        logger.error("Telegram webhook reçu mais TELEGRAM_BOT_TOKEN absent — update ignoré.")
        return JSONResponse({"received": True})

    queued = 0
    for msg in iter_inbound_messages(payload):
        queued += 1
        background.add_task(handle_inbound, msg)

    if queued == 0:
        # Update non géré (edited_message, my_chat_member, etc.) — debug only.
        logger.debug("Telegram webhook : update sans message exploitable.")

    return JSONResponse({"received": True, "queued": queued})


@router.post("/telegram/_simulate")
async def simulate_inbound(
    request: Request,
    background: BackgroundTasks,
    x_lecturai_sim: Annotated[str, Header(alias="X-Lecturai-Sim")] = "",
):
    """Route privée — injecte un faux update Telegram pour tester localement.

    Sécurité : nécessite ``TELEGRAM_SIMULATE_TOKEN`` posé en env, et header matching.
    """
    expected = (os.getenv("TELEGRAM_SIMULATE_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Simulation désactivée (TELEGRAM_SIMULATE_TOKEN absent).")
    if not x_lecturai_sim or x_lecturai_sim != expected:
        raise HTTPException(status_code=403, detail="Token simulation invalide.")
    payload = await request.json()
    queued = 0
    for msg in iter_inbound_messages(payload):
        queued += 1
        background.add_task(handle_inbound, msg)
    return {"queued": queued}
