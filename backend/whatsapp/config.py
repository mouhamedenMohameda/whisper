"""Lecture des variables d'env Meta WhatsApp Cloud API.

Variables (mettre dans ``backend/.env`` sur le VPS) :

  WHATSAPP_PHONE_NUMBER_ID    ID du numéro émetteur (chiffres). Obligatoire en mode "live".
  WHATSAPP_ACCESS_TOKEN       Token EAAxxx ou format use-case. Obligatoire pour envoyer.
  WHATSAPP_VERIFY_TOKEN       Chaîne arbitraire choisie par toi, posée aussi côté Meta lors du setup webhook.
  WHATSAPP_APP_SECRET         App Secret de l'app Meta — pour vérifier la signature HMAC X-Hub-Signature-256.
                              (Trouvable dans "App settings → Basic" sur developers.facebook.com.)
  WHATSAPP_GRAPH_API_VERSION  Défaut ``v23.0``. Surcharge possible si Meta force une montée de version.
  WHATSAPP_DRY_RUN            ``true`` → toutes les requêtes sortantes vers Meta sont log-only (test local sans crédits).
  WHATSAPP_DEFAULT_LANG       Défaut ``fr``. Langue des messages bot par défaut (override par user.ui_locale si dispo).

Garde-fous :
  - ``has_credentials()`` retourne False tant qu'une des 3 valeurs critiques manque → webhook répond 503.
  - ``is_dry_run()`` court-circuite l'envoi en prod (utile pour MVP / tests).
"""

from __future__ import annotations

import os
from typing import Optional


def _strip(name: str) -> str:
    return (os.getenv(name) or "").strip()


def phone_number_id() -> str:
    return _strip("WHATSAPP_PHONE_NUMBER_ID")


def access_token() -> str:
    return _strip("WHATSAPP_ACCESS_TOKEN")


def verify_token() -> str:
    return _strip("WHATSAPP_VERIFY_TOKEN")


def app_secret() -> str:
    return _strip("WHATSAPP_APP_SECRET")


def graph_api_version() -> str:
    return _strip("WHATSAPP_GRAPH_API_VERSION") or "v23.0"


def is_dry_run() -> bool:
    return _strip("WHATSAPP_DRY_RUN").lower() in ("1", "true", "yes", "on")


def default_language() -> str:
    raw = _strip("WHATSAPP_DEFAULT_LANG").lower()
    return "ar" if raw.startswith("ar") else "fr"


def public_app_base_url() -> str:
    return (os.getenv("PUBLIC_APP_BASE_URL") or "").strip().rstrip("/")


def has_credentials() -> bool:
    """Vérifie que les 3 valeurs critiques d'envoi sont posées."""
    return bool(phone_number_id() and access_token())


def has_inbound_security() -> bool:
    """Vérifie qu'on peut sécuriser le webhook entrant (verify + signature)."""
    return bool(verify_token() and app_secret())


def graph_base_url() -> str:
    return f"https://graph.facebook.com/{graph_api_version()}"


def phone_messages_url() -> str:
    return f"{graph_base_url()}/{phone_number_id()}/messages"


def phone_media_url() -> str:
    return f"{graph_base_url()}/{phone_number_id()}/media"


def media_object_url(media_id: str) -> str:
    return f"{graph_base_url()}/{media_id}"


def signup_url() -> Optional[str]:
    """URL d'inscription qu'on envoie aux numéros inconnus pour devenir user."""
    base = public_app_base_url()
    return base if base else None


def topup_url() -> Optional[str]:
    """URL profonde vers la page Crédits."""
    base = public_app_base_url()
    return f"{base}/?topup=1" if base else None
