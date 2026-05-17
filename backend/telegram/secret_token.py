"""Vérification du header ``X-Telegram-Bot-Api-Secret-Token``.

Contrairement à WhatsApp (HMAC du body), Telegram fait simple : le bot enregistre un
``secret_token`` côté ``setWebhook`` ; Telegram le ré-émet **identique** dans le header
à chaque POST. On compare en temps constant. Doc : https://core.telegram.org/bots/api#setwebhook

Sans secret côté serveur (config absente), n'importe qui peut forger des updates et
faire débiter de vrais users → fraude. En dev local on log un warning fort.
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


def verify(header_value: Optional[str]) -> bool:
    expected = config.webhook_secret()
    if not expected:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET non configuré — le webhook accepte tout. "
            "Configure cette variable en production pour bloquer les requêtes forgées."
        )
        return True
    if not header_value:
        return False
    return hmac.compare_digest(header_value.strip(), expected)
