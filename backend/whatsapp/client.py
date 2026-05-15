"""Client HTTP minimal pour la Meta WhatsApp Cloud API.

Surface minimale dont le bot a besoin :
  - ``send_text``        : message texte simple (réponse / instructions).
  - ``send_document``    : envoie un PDF (la leçon générée) au numéro du user.
  - ``download_media``   : récupère un audio/voice envoyé par l'utilisateur (2 étapes : URL puis binaire).

Si ``WHATSAPP_DRY_RUN=true``, toutes les méthodes loggent l'action et renvoient un faux ID — pratique
pour développer localement sans consommer le quota Meta ni payer la 1ère conversation.

Erreurs : on raise ``WhatsAppApiError`` pour qu'appelants distinguent un bug réseau d'un blocage
business (ex : la fenêtre de 24h est fermée et il faut un template).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, read=60.0, connect=10.0)
# Limite stricte coté Meta pour un media téléchargé via WhatsApp : audio ~16 MB, document 100 MB.
# On garde une borne raisonnable côté bot pour éviter de surcharger le worker transcription.
MEDIA_DOWNLOAD_MAX_BYTES = int(os.getenv("WHATSAPP_MAX_MEDIA_BYTES", str(60 * 1024 * 1024)))


class WhatsAppApiError(RuntimeError):
    """Erreur métier renvoyée par l'API Meta (différent d'une panne réseau ponctuelle)."""

    def __init__(self, message: str, *, status: Optional[int] = None, code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


def _auth_headers() -> dict[str, str]:
    tok = config.access_token()
    if not tok:
        raise WhatsAppApiError("WHATSAPP_ACCESS_TOKEN non configuré.")
    return {"Authorization": f"Bearer {tok}"}


def _raise_for_meta(resp: httpx.Response) -> None:
    if 200 <= resp.status_code < 300:
        return
    payload: Any = None
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    msg = err.get("message") or str(payload)[:240] or "Erreur Meta inconnue"
    raise WhatsAppApiError(
        f"Meta API {resp.status_code}: {msg}",
        status=resp.status_code,
        code=err.get("code") if isinstance(err, dict) else None,
        payload=payload,
    )


async def send_text(to_phone: str, body: str) -> str:
    """Envoie un message texte. Retourne le ``wamid`` (ID Meta du message envoyé)."""
    if not to_phone or not body:
        raise WhatsAppApiError("to_phone et body sont obligatoires.")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4000]},
    }
    if config.is_dry_run():
        logger.info("[whatsapp dry-run] send_text to=%s body=%r", to_phone, body[:200])
        return "dryrun-text"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.phone_messages_url(), headers=_auth_headers(), json=payload)
    _raise_for_meta(resp)
    data = resp.json()
    return _extract_message_id(data) or ""


async def upload_document(local_path: Path, mime_type: str = "application/pdf") -> str:
    """Upload un fichier vers Meta. Retourne le ``media_id`` à utiliser dans ``send_document``."""
    if not local_path.exists():
        raise WhatsAppApiError(f"Fichier introuvable : {local_path}")
    if config.is_dry_run():
        logger.info("[whatsapp dry-run] upload_document path=%s mime=%s", local_path, mime_type)
        return "dryrun-media-id"
    headers = _auth_headers()
    with local_path.open("rb") as f:
        files = {
            "file": (local_path.name, f, mime_type),
            "type": (None, mime_type),
            "messaging_product": (None, "whatsapp"),
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(config.phone_media_url(), headers=headers, files=files)
    _raise_for_meta(resp)
    data = resp.json()
    media_id = data.get("id")
    if not media_id:
        raise WhatsAppApiError("Réponse upload_document sans ``id``.", payload=data)
    return str(media_id)


async def send_document(to_phone: str, media_id: str, filename: str, caption: str = "") -> str:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "document",
        "document": {
            "id": media_id,
            "filename": filename[:120],
        },
    }
    if caption:
        payload["document"]["caption"] = caption[:1024]
    if config.is_dry_run():
        logger.info("[whatsapp dry-run] send_document to=%s media_id=%s file=%s", to_phone, media_id, filename)
        return "dryrun-doc"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.phone_messages_url(), headers=_auth_headers(), json=payload)
    _raise_for_meta(resp)
    data = resp.json()
    return _extract_message_id(data) or ""


async def send_interactive_buttons(
    to_phone: str,
    body: str,
    buttons: list[tuple[str, str]],
    header: Optional[str] = None,
    footer: Optional[str] = None,
) -> str:
    """Envoie un message interactif avec 1–3 *reply buttons* (Meta limite stricte).

    ``buttons`` = liste de tuples ``(button_id, label)``. ``button_id`` revient dans le webhook
    sous ``interactive.button_reply.id`` — c'est ce qu'on utilise pour router la réponse côté processor.
    """
    if not buttons or len(buttons) > 3:
        raise WhatsAppApiError("send_interactive_buttons: 1 à 3 boutons requis.")
    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": body[:1024]},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}}
                for bid, title in buttons
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "interactive",
        "interactive": interactive,
    }
    if config.is_dry_run():
        logger.info("[whatsapp dry-run] send_interactive_buttons to=%s body=%r buttons=%s", to_phone, body[:120], buttons)
        return "dryrun-interactive-btn"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.phone_messages_url(), headers=_auth_headers(), json=payload)
    _raise_for_meta(resp)
    return _extract_message_id(resp.json()) or ""


async def send_interactive_list(
    to_phone: str,
    body: str,
    button_label: str,
    rows: list[tuple],
    header: Optional[str] = None,
    footer: Optional[str] = None,
    section_title: str = "Options",
) -> str:
    """Envoie un message interactif avec une liste (jusqu'à 10 lignes).

    ``rows`` accepte deux formes :
      - ``(row_id, title)`` — title affiché (24 chars max)
      - ``(row_id, title, description)`` — title (24 chars) + description (72 chars)

    Pour des libellés longs (quiz à options détaillées), passer la version 3-tuple est
    indispensable car WhatsApp tronque silencieusement le ``title`` au-delà de 24 chars.
    """
    if not rows or len(rows) > 10:
        raise WhatsAppApiError("send_interactive_list: 1 à 10 lignes requises.")

    rows_payload: list[dict[str, str]] = []
    for r in rows:
        if len(r) == 2:
            rid, title = r
            desc = None
        elif len(r) == 3:
            rid, title, desc = r
        else:
            raise WhatsAppApiError("send_interactive_list: chaque ligne doit avoir 2 ou 3 éléments.")
        row = {"id": str(rid)[:200], "title": str(title)[:24]}
        if desc:
            row["description"] = str(desc)[:72]
        rows_payload.append(row)

    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": body[:1024]},
        "action": {
            "button": button_label[:20],
            "sections": [
                {
                    "title": section_title[:24],
                    "rows": rows_payload,
                }
            ],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "interactive",
        "interactive": interactive,
    }
    if config.is_dry_run():
        logger.info("[whatsapp dry-run] send_interactive_list to=%s body=%r rows=%s", to_phone, body[:120], rows)
        return "dryrun-interactive-list"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.phone_messages_url(), headers=_auth_headers(), json=payload)
    _raise_for_meta(resp)
    return _extract_message_id(resp.json()) or ""


async def mark_as_read(message_id: str) -> None:
    """Marque un message entrant comme lu (les doubles ✓ bleus). Best-effort — n'explose jamais."""
    if not message_id:
        return
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    if config.is_dry_run():
        logger.info("[whatsapp dry-run] mark_as_read id=%s", message_id)
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(config.phone_messages_url(), headers=_auth_headers(), json=payload)
        if resp.status_code >= 400:
            logger.debug("mark_as_read non-2xx: %s %s", resp.status_code, resp.text[:200])
    except Exception:
        logger.debug("mark_as_read échoué id=%s", message_id, exc_info=True)


async def _fetch_media_url(media_id: str) -> tuple[str, Optional[str]]:
    """Étape 1 : récupère l'URL CDN temporaire + mime_type du média."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(config.media_object_url(media_id), headers=_auth_headers())
    _raise_for_meta(resp)
    data = resp.json()
    url = data.get("url")
    mime = data.get("mime_type")
    if not url:
        raise WhatsAppApiError("Réponse média sans URL CDN.", payload=data)
    return str(url), (str(mime) if mime else None)


async def download_media(media_id: str, dest_path: Path) -> tuple[Path, Optional[str]]:
    """Étape 2 : télécharge le binaire vers ``dest_path``. Retourne (path, mime_type)."""
    if config.is_dry_run():
        logger.info("[whatsapp dry-run] download_media id=%s dest=%s — pas de download réel", media_id, dest_path)
        # En dry-run on ne peut pas créer un fichier audio crédible — appelant doit gérer.
        return dest_path, None
    url, mime = await _fetch_media_url(media_id)
    # Téléchargement en streaming pour ne pas charger 60 MB en RAM.
    written = 0
    with dest_path.open("wb") as f:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0, connect=10.0)) as client:
            async with client.stream("GET", url, headers=_auth_headers()) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise WhatsAppApiError(
                        f"Téléchargement média {resp.status_code}",
                        status=resp.status_code,
                        payload=body[:240],
                    )
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MEDIA_DOWNLOAD_MAX_BYTES:
                        raise WhatsAppApiError(
                            f"Média trop volumineux (> {MEDIA_DOWNLOAD_MAX_BYTES // (1024*1024)} MB).",
                            status=413,
                        )
                    f.write(chunk)
    return dest_path, mime


def _extract_message_id(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            mid = first.get("id")
            if isinstance(mid, str):
                return mid
    return None
