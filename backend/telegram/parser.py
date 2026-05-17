"""Parse les Updates Telegram en une structure normalisée.

Doc payload : https://core.telegram.org/bots/api#update

On normalise ``message`` (texte, voice, audio, document) **et** ``callback_query``
(boutons inline) dans un même dataclass pour que ``processor`` ait une surface unique.

Types ignorés silencieusement : edited_message, channel_post, inline_query,
chosen_inline_result, my_chat_member, etc. (pas pertinents pour ce bot 1-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass
class InboundTgMessage:
    chat_id: str          # ID numérique Telegram (positif = user 1-1, négatif = groupe). On stocke en str.
    message_id: str       # update_id de l'update parent — sert au dédoublonnage (Telegram peut retenter).
    type: str             # "text" | "voice" | "audio" | "document" | "callback"
    text: Optional[str] = None              # corps texte (commande, /lier, ou texte libre)
    file_id: Optional[str] = None           # pour voice/audio/document : ID Telegram à passer à getFile
    file_mime: Optional[str] = None         # mime_type fourni par Telegram (peut être absent pour voice)
    file_size: Optional[int] = None         # octets — pour pré-filtrer les médias trop gros
    profile_name: Optional[str] = None      # first_name + last_name du sender (logs uniquement)
    callback_id: Optional[str] = None       # ID de la callback_query — requis pour answerCallbackQuery
    callback_data: Optional[str] = None     # data du bouton inline cliqué (ex: "quiz:0:2")


def iter_inbound_messages(payload: Any) -> Iterable[InboundTgMessage]:
    """Génère 0 ou 1 message exploitable depuis un update Telegram.

    Tolérant : payload corrompu → rien yielded. Un seul update par POST côté Telegram,
    mais on garde la signature ``Iterable`` pour rester symétrique avec WhatsApp.
    """
    if not isinstance(payload, dict):
        return
    update_id = payload.get("update_id")
    if not isinstance(update_id, int):
        return
    update_id_str = str(update_id)

    # === Callback_query (clic sur bouton inline) ===
    cb = payload.get("callback_query")
    if isinstance(cb, dict):
        cb_id = cb.get("id")
        cb_data = cb.get("data")
        from_user = cb.get("from") if isinstance(cb.get("from"), dict) else {}
        msg = cb.get("message") if isinstance(cb.get("message"), dict) else {}
        chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = chat.get("id")
        if isinstance(chat_id, int) and isinstance(cb_id, str):
            yield InboundTgMessage(
                chat_id=str(chat_id),
                message_id=update_id_str,
                type="callback",
                text=None,
                profile_name=_profile_name(from_user),
                callback_id=cb_id,
                callback_data=str(cb_data) if isinstance(cb_data, str) else None,
            )
        return

    # === Message normal (texte, voice, audio, document) ===
    msg = payload.get("message")
    if not isinstance(msg, dict):
        return
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return
    chat_id_str = str(chat_id)
    from_user = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    profile = _profile_name(from_user)

    # Voice : message vocal OGG (le plus courant)
    voice = msg.get("voice")
    if isinstance(voice, dict) and isinstance(voice.get("file_id"), str):
        yield InboundTgMessage(
            chat_id=chat_id_str,
            message_id=update_id_str,
            type="voice",
            file_id=voice["file_id"],
            file_mime=voice.get("mime_type") if isinstance(voice.get("mime_type"), str) else None,
            file_size=voice.get("file_size") if isinstance(voice.get("file_size"), int) else None,
            profile_name=profile,
        )
        return

    # Audio : fichier audio attaché (MP3, M4A, etc.)
    audio = msg.get("audio")
    if isinstance(audio, dict) and isinstance(audio.get("file_id"), str):
        yield InboundTgMessage(
            chat_id=chat_id_str,
            message_id=update_id_str,
            type="audio",
            file_id=audio["file_id"],
            file_mime=audio.get("mime_type") if isinstance(audio.get("mime_type"), str) else None,
            file_size=audio.get("file_size") if isinstance(audio.get("file_size"), int) else None,
            profile_name=profile,
        )
        return

    # Document : on accepte si c'est un fichier audio déposé en pièce-jointe (mime audio/*).
    doc = msg.get("document")
    if isinstance(doc, dict) and isinstance(doc.get("file_id"), str):
        mime = doc.get("mime_type") if isinstance(doc.get("mime_type"), str) else None
        if mime and (mime.startswith("audio/") or mime.startswith("video/")):
            yield InboundTgMessage(
                chat_id=chat_id_str,
                message_id=update_id_str,
                type="document",
                file_id=doc["file_id"],
                file_mime=mime,
                file_size=doc.get("file_size") if isinstance(doc.get("file_size"), int) else None,
                profile_name=profile,
            )
            return
        # Document non-audio → ignoré (sera traité comme "type non supporté" en amont si on yield text).

    # Texte (commande ou message libre)
    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        yield InboundTgMessage(
            chat_id=chat_id_str,
            message_id=update_id_str,
            type="text",
            text=text.strip(),
            profile_name=profile,
        )
        return


def _profile_name(from_user: dict) -> Optional[str]:
    if not isinstance(from_user, dict):
        return None
    first = from_user.get("first_name") if isinstance(from_user.get("first_name"), str) else ""
    last = from_user.get("last_name") if isinstance(from_user.get("last_name"), str) else ""
    full = (first + " " + last).strip()
    return full or None
