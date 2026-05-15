"""Parse les payloads webhook entrants Meta WhatsApp en une structure normalisée.

Doc payload : https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples

Forme attendue (très abrégée) :
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<WABA_ID>",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "...", "display_phone_number": "..."},
        "contacts": [{"profile": {"name": "..."}, "wa_id": "22241xxxxxx"}],
        "messages": [{"from": "22241xxxxxx", "id": "wamid....", "type": "audio"|"voice"|"text", "audio": {...}, "text": {...}, "timestamp": "..."}]
      }
    }]
  }]
}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass
class InboundMessage:
    wa_id: str                 # numéro WhatsApp de l'expéditeur (sans +)
    message_id: str            # wamid.xxx (idempotence / dédup)
    type: str                  # "audio" | "voice" | "text" | "image" | ...
    text: Optional[str]
    media_id: Optional[str]
    media_mime: Optional[str]
    profile_name: Optional[str]
    timestamp: Optional[str]
    # Pour les types "interactive" (button_reply / list_reply) : id du bouton/ligne cliqué.
    interactive_id: Optional[str] = None
    interactive_title: Optional[str] = None

    @property
    def e164_phone(self) -> str:
        """Reformatage E.164 avec '+' attendu par notre table users.whatsapp_phone."""
        return f"+{self.wa_id}" if self.wa_id and not self.wa_id.startswith("+") else (self.wa_id or "")


def iter_inbound_messages(payload: Any) -> Iterable[InboundMessage]:
    """Génère les messages utilisateurs trouvés dans un payload webhook.

    Tolérant aux variations (statuts de livraison, messages système, payload corrompu) :
    on yield uniquement ce qui ressemble à un vrai message utilisateur exploitable.
    """
    if not isinstance(payload, dict):
        return
    if payload.get("object") != "whatsapp_business_account":
        return
    entries = payload.get("entry") or []
    if not isinstance(entries, list):
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            contacts = value.get("contacts") or []
            profile_by_wa: dict[str, str] = {}
            if isinstance(contacts, list):
                for c in contacts:
                    if not isinstance(c, dict):
                        continue
                    wa = c.get("wa_id")
                    prof = (c.get("profile") or {}).get("name") if isinstance(c.get("profile"), dict) else None
                    if isinstance(wa, str) and isinstance(prof, str):
                        profile_by_wa[wa] = prof

            messages = value.get("messages") or []
            if not isinstance(messages, list):
                continue
            for m in messages:
                if not isinstance(m, dict):
                    continue
                wa_id = m.get("from")
                msg_id = m.get("id")
                msg_type = m.get("type")
                if not isinstance(wa_id, str) or not isinstance(msg_id, str) or not isinstance(msg_type, str):
                    continue

                text_body: Optional[str] = None
                media_id: Optional[str] = None
                media_mime: Optional[str] = None
                interactive_id: Optional[str] = None
                interactive_title: Optional[str] = None

                if msg_type == "text":
                    t = m.get("text")
                    if isinstance(t, dict):
                        b = t.get("body")
                        if isinstance(b, str):
                            text_body = b.strip()
                elif msg_type in ("audio", "voice"):
                    media = m.get(msg_type)
                    if isinstance(media, dict):
                        mid = media.get("id")
                        if isinstance(mid, str):
                            media_id = mid
                        mm = media.get("mime_type")
                        if isinstance(mm, str):
                            media_mime = mm
                elif msg_type == "interactive":
                    inter = m.get("interactive")
                    if isinstance(inter, dict):
                        sub = inter.get("type")
                        node = inter.get(sub) if isinstance(sub, str) else None
                        if isinstance(node, dict):
                            rid = node.get("id")
                            rtitle = node.get("title")
                            if isinstance(rid, str):
                                interactive_id = rid
                            if isinstance(rtitle, str):
                                interactive_title = rtitle
                elif msg_type == "document":
                    # Document : on accepte uniquement si c'est un audio/video déposé en tant que document.
                    media = m.get("document")
                    if isinstance(media, dict):
                        mid = media.get("id")
                        if isinstance(mid, str):
                            media_id = mid
                        mm = media.get("mime_type")
                        if isinstance(mm, str):
                            media_mime = mm
                # Les autres types (image, sticker, location, contacts, reaction, button) → ignorés silencieusement.

                yield InboundMessage(
                    wa_id=wa_id,
                    message_id=msg_id,
                    type=msg_type,
                    text=text_body,
                    media_id=media_id,
                    media_mime=media_mime,
                    profile_name=profile_by_wa.get(wa_id),
                    timestamp=m.get("timestamp") if isinstance(m.get("timestamp"), str) else None,
                    interactive_id=interactive_id,
                    interactive_title=interactive_title,
                )
