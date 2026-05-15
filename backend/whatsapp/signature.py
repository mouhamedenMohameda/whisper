"""Vérification HMAC SHA-256 du webhook Meta WhatsApp.

Meta signe chaque POST avec ``X-Hub-Signature-256: sha256=<hex>``, calculé sur le body brut
avec ``WHATSAPP_APP_SECRET`` comme clé. Sans cette vérification, n'importe qui sur internet
peut POSTer des faux messages à notre webhook → exécution gratuite de transcriptions / faux
crédits débités sur de vrais users → fraude.

Doc Meta : https://developers.facebook.com/docs/graph-api/webhooks/getting-started#verification-requests
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


def verify(raw_body: bytes, header_value: Optional[str]) -> bool:
    """Retourne True si la signature ``X-Hub-Signature-256`` correspond au body avec le secret app.

    En mode développement (pas de ``WHATSAPP_APP_SECRET`` configuré), on log un warning fort
    mais on laisse passer — utile pour `/api/whatsapp/_simulate` en local. En prod, **configurer
    le secret est obligatoire** (sinon le webhook accepte tout).
    """
    secret = config.app_secret()
    if not secret:
        logger.warning(
            "WHATSAPP_APP_SECRET non configuré — la signature HMAC du webhook n'est PAS vérifiée. "
            "Configure cette variable en production pour bloquer les requêtes forgées."
        )
        return True
    if not header_value or not header_value.startswith("sha256="):
        return False
    received_hex = header_value.split("=", 1)[1].strip().lower()
    expected_hex = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest().lower()
    # ``compare_digest`` : protection contre les attaques par mesure de temps.
    return hmac.compare_digest(received_hex, expected_hex)
