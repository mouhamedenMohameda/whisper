"""Client HTTP minimal pour la Bot API Telegram.

Surface utilisée par le bot :
  - ``send_text``               : message texte (Markdown par défaut, fallback plain si échec).
  - ``send_document_bytes``     : envoie un PDF/TXT depuis un buffer en mémoire (pas d'upload pré-alloué).
  - ``download_file``           : 2 étapes — getFile (file_path CDN) puis GET binaire en streaming.
  - ``send_inline_keyboard``    : envoie un message avec boutons inline (quiz, menus).
  - ``answer_callback_query``   : ACK un clic bouton (oblige sinon le loader tourne 30s).

Si ``TELEGRAM_DRY_RUN=true``, toutes les méthodes loggent et renvoient des IDs factices.

Erreurs : on raise ``TelegramApiError`` pour distinguer un bug réseau d'un blocage métier
(bot bloqué par l'user, chat introuvable, fichier trop gros, etc.).
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, read=60.0, connect=10.0)
# Plafond ``getFile`` côté Bot API standard = **20 MB**, hard cap imposé par Telegram (peu importe
# le ``mime_type``). Au-delà, l'API renvoie 400 "Bad Request: file is too big". On lève ce cap en
# pointant ``TELEGRAM_API_BASE`` sur un serveur ``telegram-bot-api`` self-hosted (jusqu'à 2 GB).
# Voir : https://core.telegram.org/bots/api#getfile
TELEGRAM_GETFILE_HARD_LIMIT_BYTES = 20 * 1024 * 1024


def max_media_bytes() -> int:
    """Limite locale qu'on impose en plus du cap Telegram (anti-DoS / espace disque).

    Cloud API standard : 19 MB (un peu sous le hard cap de 20).
    Local API server   : 500 MB par défaut (le hard cap Telegram passe à 2 GB, mais 500 MB
                         couvre largement un cours de 5h+ et évite qu'un user envoie un .iso).
    Surchargeable via ``TELEGRAM_MAX_MEDIA_BYTES`` (en bytes).

    **Lazy** : appelé à chaque check pour respecter les env vars chargées tardivement par
    ``load_dotenv()`` dans ``main.py`` (les imports du module Telegram peuvent précéder
    le ``load_dotenv``, auquel cas un snapshot pris à l'import serait obsolète).
    """
    override = os.getenv("TELEGRAM_MAX_MEDIA_BYTES", "").strip()
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return 500 * 1024 * 1024 if config.is_local_api_mode() else 19 * 1024 * 1024


# Compat : certains appelants externes lisaient ``client.MEDIA_DOWNLOAD_MAX_BYTES`` directement.
# On garde le nom mais on transforme en property via ``__getattr__`` (PEP 562) — chaque accès
# recompute la valeur.
def __getattr__(name: str):  # pragma: no cover — surface compat
    if name == "MEDIA_DOWNLOAD_MAX_BYTES":
        return max_media_bytes()
    raise AttributeError(name)


class TelegramApiError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None, code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


def _raise_for_tg(resp: httpx.Response) -> dict:
    """Vérifie le statut HTTP + le champ ``ok`` du body. Retourne ``result`` si OK."""
    try:
        payload = resp.json()
    except Exception:
        payload = {"description": resp.text[:240]}
    if 200 <= resp.status_code < 300 and isinstance(payload, dict) and payload.get("ok"):
        result = payload.get("result")
        return result if isinstance(result, dict) else {}
    desc = (payload.get("description") if isinstance(payload, dict) else None) or "Erreur Telegram inconnue"
    code = payload.get("error_code") if isinstance(payload, dict) else None
    raise TelegramApiError(
        f"Telegram API {resp.status_code}: {desc}",
        status=resp.status_code,
        code=code,
        payload=payload,
    )


async def send_text(chat_id: str, body: str, *, parse_mode: Optional[str] = "Markdown") -> str:
    """Envoie un message texte. Retourne l'ID du message envoyé (str).

    ``parse_mode='Markdown'`` permet les ``*gras*`` utilisés par ``messages.t()``. Si Telegram refuse
    le parse (entité mal formée dans le texte de l'user injecté dans un template), on retente en plain.
    """
    if not chat_id or not body:
        raise TelegramApiError("chat_id et body sont obligatoires.")
    # Telegram plafonne un message texte à 4096 chars — on coupe pour ne jamais 400.
    payload: dict[str, Any] = {"chat_id": chat_id, "text": body[:4000], "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if config.is_dry_run():
        logger.info("[telegram dry-run] send_text chat=%s body=%r", chat_id, body[:200])
        return "dryrun-text"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.bot_api_url("sendMessage"), json=payload)
    try:
        result = _raise_for_tg(resp)
    except TelegramApiError as exc:
        # Markdown rejeté ? Retente en plain pour ne pas perdre le message.
        if parse_mode and exc.status == 400 and "parse" in (str(exc).lower()):
            payload.pop("parse_mode", None)
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp2 = await client.post(config.bot_api_url("sendMessage"), json=payload)
            result = _raise_for_tg(resp2)
        else:
            raise
    msg_id = result.get("message_id")
    return str(msg_id) if msg_id is not None else ""


async def send_document_bytes(
    chat_id: str,
    data: bytes,
    filename: str,
    *,
    caption: str = "",
    mime: str = "application/pdf",
) -> str:
    """Envoie un document en mémoire (PDF / TXT) sans étape d'upload pré-alloué.

    Telegram ``sendDocument`` accepte multipart direct — pas besoin d'``upload_document`` séparé
    comme côté WhatsApp.
    """
    if not chat_id or not data:
        raise TelegramApiError("chat_id et data sont obligatoires.")
    if config.is_dry_run():
        logger.info("[telegram dry-run] send_document_bytes chat=%s file=%s bytes=%d", chat_id, filename, len(data))
        return "dryrun-doc"
    files = {"document": (filename[:120], io.BytesIO(data), mime)}
    form: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        form["caption"] = caption[:1024]
        form["parse_mode"] = "Markdown"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=180.0, connect=10.0)) as client:
        resp = await client.post(config.bot_api_url("sendDocument"), data=form, files=files)
    result = _raise_for_tg(resp)
    msg_id = result.get("message_id")
    return str(msg_id) if msg_id is not None else ""


async def send_inline_keyboard(
    chat_id: str,
    body: str,
    buttons: list[list[tuple[str, str]]],
    *,
    parse_mode: Optional[str] = "Markdown",
) -> str:
    """Envoie un message avec clavier inline.

    ``buttons`` = matrice de tuples ``(label, callback_data)``. Une sous-liste = une rangée.
    ``callback_data`` ≤ 64 octets (limite Telegram dure). On tronque silencieusement.
    """
    if not buttons:
        raise TelegramApiError("send_inline_keyboard: au moins 1 bouton requis.")
    keyboard = [
        [{"text": label[:64], "callback_data": str(data)[:64]} for label, data in row]
        for row in buttons
        if row
    ]
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": body[:4000],
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if config.is_dry_run():
        logger.info("[telegram dry-run] send_inline_keyboard chat=%s body=%r kb=%s", chat_id, body[:120], keyboard)
        return "dryrun-kb"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.bot_api_url("sendMessage"), json=payload)
    result = _raise_for_tg(resp)
    msg_id = result.get("message_id")
    return str(msg_id) if msg_id is not None else ""


async def answer_callback_query(callback_id: str, text: str = "") -> None:
    """ACK obligatoire après un clic bouton — sinon Telegram affiche un spinner 30s côté user."""
    if not callback_id:
        return
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:200]
    if config.is_dry_run():
        logger.info("[telegram dry-run] answer_callback_query id=%s text=%r", callback_id, text[:80])
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(config.bot_api_url("answerCallbackQuery"), json=payload)
        if resp.status_code >= 400:
            logger.debug("answer_callback_query non-2xx: %s %s", resp.status_code, resp.text[:200])
    except Exception:
        logger.debug("answer_callback_query échoué id=%s", callback_id, exc_info=True)


async def _get_file_path(file_id: str) -> tuple[str, Optional[int]]:
    """Étape 1 : ``getFile`` → ``(file_path, file_size)``. ``file_path`` est un chemin relatif
    à concaténer avec ``api_base()/file/bot<token>/...`` pour le téléchargement.

    Note : pour les fichiers > 20 MB, Telegram répond 400 ``"Bad Request: file is too big"``.
    On retraduit cette erreur en ``TelegramApiError(status=413)`` pour que l'appelant traite ce cas
    comme un quota plutôt qu'une erreur réseau générique.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(config.bot_api_url("getFile"), json={"file_id": file_id})
    try:
        result = _raise_for_tg(resp)
    except TelegramApiError as exc:
        msg = str(exc).lower()
        if exc.status == 400 and "too big" in msg:
            raise TelegramApiError(
                "Fichier trop volumineux pour l'API Bot Telegram (limite 20 MB).",
                status=413, code=exc.code, payload=exc.payload,
            ) from exc
        raise
    file_path = result.get("file_path")
    if not isinstance(file_path, str):
        raise TelegramApiError("Réponse getFile sans file_path.", payload=result)
    size = result.get("file_size") if isinstance(result.get("file_size"), int) else None
    return file_path, size


async def download_file(file_id: str, dest_path: Path) -> tuple[Path, Optional[int]]:
    """Récupère le binaire du fichier Telegram vers ``dest_path``. Retourne (path, size_bytes).

    Deux modes :
      - **Cloud Bot API** (défaut) : ``getFile`` renvoie une URL CDN relative, on stream le binaire en HTTPS.
      - **Local Bot API server** (``TELEGRAM_LOCAL_API_MODE=true``) : ``getFile`` renvoie un chemin
        absolu sur le disque du daemon. On copie/déplace le fichier sans passer par HTTP.

    Limite : ``MEDIA_DOWNLOAD_MAX_BYTES`` (19 MB cloud / 500 MB local par défaut, surchargeable).
    """
    if config.is_dry_run():
        logger.info("[telegram dry-run] download_file id=%s dest=%s — pas de download réel", file_id, dest_path)
        return dest_path, None
    file_path, size_hint = await _get_file_path(file_id)
    cap = max_media_bytes()
    if size_hint is not None and size_hint > cap:
        raise TelegramApiError(
            f"Média trop volumineux ({size_hint // (1024 * 1024)} MB > {cap // (1024 * 1024)} MB).",
            status=413,
        )

    # === Mode local : file_path est un chemin absolu sur le disque ===
    # On reconnaît ce cas en regardant si la valeur commence par "/" (chemin Unix absolu) ; la version
    # cloud renvoie toujours quelque chose comme "voice/file_0.ogg" (relatif). On garde aussi
    # ``is_local_api_mode()`` comme garde-fou explicite.
    if config.is_local_api_mode() or file_path.startswith("/"):
        return await _copy_local_file(file_path, dest_path)

    # === Mode cloud : streaming HTTPS classique ===
    written = 0
    with dest_path.open("wb") as f:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0, connect=10.0)) as client:
            async with client.stream("GET", config.file_download_url(file_path)) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise TelegramApiError(
                        f"Téléchargement fichier {resp.status_code}",
                        status=resp.status_code,
                        payload=body[:240],
                    )
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > cap:
                        raise TelegramApiError(
                            f"Média trop volumineux (> {cap // (1024 * 1024)} MB).",
                            status=413,
                        )
                    f.write(chunk)
    return dest_path, written


async def _copy_local_file(src_abs_path: str, dest_path: Path) -> tuple[Path, int]:
    """Récupère un fichier produit par le local Bot API server vers ``dest_path``.

    Stratégie : ``rename`` (atomic move) si possible — sinon ``copy + unlink``. Dans les deux cas,
    le fichier disparaît de ``/var/lib/telegram-bot-api/`` après cette fonction. C'est volontaire :
    le daemon ne purge **jamais** ses fichiers tout seul, on aurait un disk leak garanti.

    Si l'unlink échoue (permissions, race), on log mais on ne plante pas — l'audio est déjà copié.
    """
    import asyncio
    import os
    import shutil

    src = Path(src_abs_path)
    if not src.exists():
        raise TelegramApiError(
            f"Local Bot API : fichier introuvable à {src_abs_path}. "
            "Vérifie le mount du volume entre le daemon et le backend.",
            status=500,
        )
    size = src.stat().st_size
    cap = max_media_bytes()
    if size > cap:
        raise TelegramApiError(
            f"Média trop volumineux ({size // (1024 * 1024)} MB > {cap // (1024 * 1024)} MB).",
            status=413,
        )

    def _move_or_copy() -> None:
        try:
            # ``os.rename`` : atomic, instantané, libère la source en une syscall — idéal.
            os.rename(str(src), str(dest_path))
            return
        except OSError:
            # Cross-device (le mount Docker peut être sur un autre filesystem que data/jobs/),
            # ou permissions différentes → fallback copy+unlink.
            shutil.copyfile(str(src), str(dest_path))
            try:
                src.unlink()
            except Exception:
                logger.warning(
                    "Local Bot API : copie OK mais impossible de supprimer la source %s — "
                    "le disque du daemon va se remplir, prévoir une cron de nettoyage.",
                    src_abs_path,
                )

    await asyncio.to_thread(_move_or_copy)
    return dest_path, size
