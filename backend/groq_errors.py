"""Messages utilisateur à partir des erreurs du SDK Groq (sans fuite de secrets)."""

from __future__ import annotations

import json
from typing import Any, Optional, Tuple


def _parse_groq_error_body(body: Any) -> Tuple[Optional[int], str]:
    if body is None:
        return None, ""
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8", "replace")
        except Exception:
            return None, str(body)[:500]
    if not isinstance(body, str) or not body.strip():
        return None, ""
    try:
        j = json.loads(body)
    except json.JSONDecodeError:
        return None, body.strip()[:500]
    if not isinstance(j, dict):
        return None, body.strip()[:500]
    err = j.get("error")
    if not isinstance(err, dict):
        return None, body.strip()[:500]
    msg = str(err.get("message") or "").strip()
    code = err.get("code")
    icode: Optional[int] = None
    if isinstance(code, int):
        icode = code
    elif isinstance(code, str) and code.strip().lstrip("-").isdigit():
        try:
            icode = int(code)
        except ValueError:
            icode = None
    return icode, msg


def is_groq_rate_limit_error(exc: BaseException) -> bool:
    """True si l’erreur ressemble à un 429 / rate limit Groq (corps JSON ou message)."""
    try:
        from groq import APIError
    except ImportError:
        return False
    if not isinstance(exc, APIError):
        return False
    code, api_msg = _parse_groq_error_body(getattr(exc, "body", None))
    if code == 429:
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8", "replace")
        except Exception:
            body = ""
    if isinstance(body, str) and body.strip().startswith("{"):
        try:
            j = json.loads(body)
            err = j.get("error") if isinstance(j, dict) else None
            if isinstance(err, dict):
                et = str(err.get("type") or "").lower()
                if "rate" in et or "limit" in et:
                    return True
        except json.JSONDecodeError:
            pass
    raw = " ".join(x for x in (api_msg, str(getattr(exc, "message", "") or "")) if x).lower()
    return any(
        k in raw
        for k in (
            "429",
            "rate limit",
            "too many requests",
            "tokens per minute",
            "requests per minute",
            "quota",
            "capacity",
        )
    )


def http_detail_for_groq_api_error(exc: BaseException) -> str:
    """Phrase courte pour affichage UI / HTTP ``detail`` (évite le message générique opaque)."""
    try:
        from groq import APIError
    except ImportError:
        return "Le moteur de cours est indisponible. Réessaie plus tard."

    if not isinstance(exc, APIError):
        return "Le moteur de cours a rencontré une erreur. Réessaie plus tard."

    _code, api_msg = _parse_groq_error_body(getattr(exc, "body", None))
    sdk_msg = str(getattr(exc, "message", "") or "").strip()
    raw = " ".join(x for x in (api_msg, sdk_msg) if x).strip()[:500]
    low = raw.lower()

    if "invalid" in low and "api" in low and "key" in low:
        return "Clé API refusée — vérifie la variable d’environnement sur le serveur."
    if "incorrect api key" in low or "invalid_api_key" in low:
        return "Clé API refusée — vérifie la variable d’environnement sur le serveur."
    if "rate" in low or "limit" in low or "too many requests" in low or "429" in raw:
        return "Quota ou limite de débit atteinte — réessaie dans quelques minutes."
    if any(
        k in low
        for k in (
            "does not exist",
            "not found",
            "decommission",
            "model_not_found",
            "invalid model",
            "unknown model",
        )
    ):
        return (
            "Modèle indisponible ou non autorisé pour ce compte. "
            f"Détail : {raw[:220]}. Ajuste GROQ_GENERATE_MODEL / GROQ_COLLAGE_MODEL (ou GROQ_INSIGHT_MODEL) sur le serveur."
        )
    if raw:
        return f"Erreur du moteur de cours : {raw[:300]}"
    return "Le moteur de cours a refusé la requête. Réessaie ou vérifie la configuration du serveur."
