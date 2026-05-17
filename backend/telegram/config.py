"""Lecture des variables d'env pour le bot Telegram.

Variables (à mettre dans ``backend/.env`` sur le VPS) :

  TELEGRAM_BOT_TOKEN          Token donné par @BotFather, format ``<bot_id>:<secret>``. **Obligatoire.**
  TELEGRAM_WEBHOOK_SECRET     Chaîne arbitraire ≥ 1 char, posée ici **et** dans ``setWebhook?secret_token=…``.
                              Telegram l'écho dans le header ``X-Telegram-Bot-Api-Secret-Token`` à chaque POST.
                              Sans secret configuré côté serveur, n'importe qui peut forger des updates → fraude.
  TELEGRAM_API_BASE           Défaut ``https://api.telegram.org``. Surcharge utile pour tests locaux.
  TELEGRAM_DRY_RUN            ``true`` → toutes les requêtes sortantes sont log-only.
  TELEGRAM_DEFAULT_LANG       Défaut ``fr``. Langue de fallback si user n'a pas encore choisi.
  TELEGRAM_MAX_MEDIA_BYTES    Défaut 60 MB — limite par fichier téléchargé via getFile (Telegram plafonne à 20 MB
                              côté bot pour les fichiers normaux ; on garde large pour les ``audio`` qui peuvent
                              monter à 50 MB via le serveur Telegram).

Garde-fous :
  - ``has_credentials()`` → False tant que TELEGRAM_BOT_TOKEN absent (webhook répond 503).
  - ``is_dry_run()``      → court-circuite l'API en local sans toucher Telegram.
"""

from __future__ import annotations

import os


def _strip(name: str) -> str:
    return (os.getenv(name) or "").strip()


def bot_token() -> str:
    return _strip("TELEGRAM_BOT_TOKEN")


def webhook_secret() -> str:
    return _strip("TELEGRAM_WEBHOOK_SECRET")


def api_base() -> str:
    return _strip("TELEGRAM_API_BASE") or "https://api.telegram.org"


def is_local_api_mode() -> bool:
    """``True`` si le bot pointe sur un serveur ``telegram-bot-api`` self-hosted.

    En mode local :
      - Pas de cap 20 MB sur ``getFile`` (jusqu'à 2 GB côté Telegram)
      - ``getFile`` renvoie ``file_path`` = chemin absolu sur le disque (pas une URL CDN)
      - Le bot doit avoir été ``logOut`` du cloud avant d'utiliser le serveur local

    Activer via ``TELEGRAM_LOCAL_API_MODE=true`` dans le .env.
    """
    return _strip("TELEGRAM_LOCAL_API_MODE").lower() in ("1", "true", "yes", "on")


def is_dry_run() -> bool:
    return _strip("TELEGRAM_DRY_RUN").lower() in ("1", "true", "yes", "on")


def default_language() -> str:
    raw = _strip("TELEGRAM_DEFAULT_LANG").lower()
    return "ar" if raw.startswith("ar") else "fr"


def has_credentials() -> bool:
    return bool(bot_token())


def has_inbound_security() -> bool:
    """``True`` si le secret du webhook est posé (vérification cryptographique simple)."""
    return bool(webhook_secret())


def bot_api_url(method: str) -> str:
    """URL d'appel à une méthode Bot API (ex: ``sendMessage``, ``getFile``)."""
    return f"{api_base()}/bot{bot_token()}/{method}"


def file_download_url(file_path: str) -> str:
    """URL CDN pour télécharger un fichier après ``getFile`` (file_path renvoyé par l'API)."""
    return f"{api_base()}/file/bot{bot_token()}/{file_path.lstrip('/')}"


def public_app_base_url() -> str:
    return (os.getenv("PUBLIC_APP_BASE_URL") or "").strip().rstrip("/")


def signup_url() -> str | None:
    base = public_app_base_url()
    return base if base else None


def topup_url() -> str | None:
    base = public_app_base_url()
    return f"{base}/?topup=1" if base else None
