"""Webhook Meta WhatsApp Cloud API.

Trois endpoints :
  - ``GET  /api/whatsapp/webhook`` : challenge de vérification posé par Meta lors du setup.
  - ``POST /api/whatsapp/webhook`` : réception des événements (messages entrants, statuts).
  - ``POST /api/whatsapp/_simulate`` : route privée (header ``X-Lecturai-Sim``) pour injecter
    un faux payload Meta — utile pour tests locaux sans tunnel ngrok.

Contraintes Meta :
  - Réponse webhook < 5s sinon Meta réessaie et finit par désactiver l'abonnement.
  - On parse → on enqueue background → on ACK 200 immédiatement.
  - Doublons : Meta peut retenter, on dédoublonne par ``message_id`` côté processor.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from whatsapp import config as wa_config
from whatsapp.parser import iter_inbound_messages
from whatsapp.processor import handle_inbound
from whatsapp.signature import verify as verify_signature

logger = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp"])


@router.get("/whatsapp/webhook")
def verify_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Vérification initiale du webhook par Meta (une seule fois lors du setup).

    Meta envoie : GET /webhook?hub.mode=subscribe&hub.verify_token=<TOKEN>&hub.challenge=<XX>
    On doit retourner ``hub_challenge`` en clair si le token correspond à ``WHATSAPP_VERIFY_TOKEN``.
    """
    expected = wa_config.verify_token()
    if not expected:
        logger.warning("WHATSAPP_VERIFY_TOKEN absent — la vérification webhook échouera.")
        raise HTTPException(status_code=503, detail="WhatsApp verify token non configuré.")
    if hub_mode == "subscribe" and hub_verify_token == expected:
        return PlainTextResponse(hub_challenge or "ok")
    raise HTTPException(status_code=403, detail="Verify token invalide.")


@router.post("/whatsapp/webhook")
async def receive_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: Annotated[str, Header(alias="X-Hub-Signature-256")] = "",
):
    """Réception d'événements Meta. ACK rapide + background pour traiter."""
    # Lit le body brut (nécessaire pour HMAC).
    raw = await request.body()

    if not verify_signature(raw, x_hub_signature_256):
        logger.warning("WhatsApp webhook : signature HMAC invalide — payload ignoré.")
        # Pour ne pas révéler de signal à un attaquant, on renvoie 200 plutôt que 403 (Meta s'en fiche).
        return JSONResponse({"received": True})

    try:
        payload = await request.json()
    except Exception:
        logger.warning("WhatsApp webhook : JSON invalide.")
        return JSONResponse({"received": True})

    # Si les credentials envoi ne sont pas posés, on n'enqueue rien (sinon échec silencieux côté processor).
    if not wa_config.has_credentials() and not wa_config.is_dry_run():
        logger.error("WhatsApp webhook reçu mais credentials envoi absents (WHATSAPP_PHONE_NUMBER_ID/ACCESS_TOKEN).")
        return JSONResponse({"received": True})

    inbound_count = 0
    for msg in iter_inbound_messages(payload):
        inbound_count += 1
        # Capture les variables par défaut pour éviter le piège closure-in-loop.
        background.add_task(handle_inbound, msg)

    if inbound_count == 0:
        # Probablement un événement "status" (delivered/read) — on log discret pour le debug.
        logger.debug("WhatsApp webhook : événement sans message utilisateur (status?).")

    return JSONResponse({"received": True, "queued": inbound_count})


@router.post("/whatsapp/_simulate")
async def simulate_inbound(
    request: Request,
    background: BackgroundTasks,
    x_lecturai_sim: Annotated[str, Header(alias="X-Lecturai-Sim")] = "",
):
    """Route privée — injecte un faux payload Meta pour tester localement sans tunnel ngrok.

    Sécurité : nécessite que ``WHATSAPP_SIMULATE_TOKEN`` soit posé en env et que le header
    ``X-Lecturai-Sim`` corresponde. Aucune signature HMAC requise.
    """
    expected = (os.getenv("WHATSAPP_SIMULATE_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Simulation désactivée (WHATSAPP_SIMULATE_TOKEN absent).")
    if not x_lecturai_sim or x_lecturai_sim != expected:
        raise HTTPException(status_code=403, detail="Token simulation invalide.")
    payload = await request.json()
    queued = 0
    for msg in iter_inbound_messages(payload):
        queued += 1
        background.add_task(handle_inbound, msg)
    return {"queued": queued}
