"""Tarification côté utilisateur : USD fournisseur → MRU facturés avec marge et taux.

1 USD = MRU_PER_USD (défaut 40). Prix client (MRU) = USD_fournisseur × MRU_PER_USD × MARGIN.

Le solde en base (`credit_balance`) est un entier : MRU × MRU_WALLET_MICRO (défaut micro = 10_000 → 4 décimales).
"""

from __future__ import annotations

import math
import os


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _wallet_micro() -> int:
    raw = os.getenv("MRU_WALLET_MICRO")
    if raw is None or raw.strip() == "":
        return 10_000
    try:
        v = int(raw)
        return max(100, min(v, 1_000_000_000))
    except ValueError:
        return 10_000


MRU_WALLET_MICRO = _wallet_micro()

# Billable multiplier (marge client sur le coût USD fournisseur)
MRU_PER_USD = _f("MRU_PER_USD", 40.0)
MARGIN_MULTIPLIER = _f("CUSTOMER_MARGIN_MULTIPLIER", 3.0)

OPENAI_WHISPER_USD_PER_MINUTE = _f("OPENAI_WHISPER_USD_PER_MINUTE", 0.006)

# GPT (chat completions) utilisé sur /transcribe : polish + vue langues — prix fournisseur au million de tokens (ajuste selon tarif réel du modèle).
OPENAI_TRANSCRIBE_CHAT_INPUT_USD_PER_MTOK = _f("OPENAI_TRANSCRIBE_CHAT_INPUT_USD_PER_MTOK", 0.15)
OPENAI_TRANSCRIBE_CHAT_OUTPUT_USD_PER_MTOK = _f("OPENAI_TRANSCRIBE_CHAT_OUTPUT_USD_PER_MTOK", 0.60)

ANTHROPIC_INPUT_USD_PER_MTOK = _f("ANTHROPIC_INPUT_USD_PER_MTOK", 3.0)
ANTHROPIC_OUTPUT_USD_PER_MTOK = _f("ANTHROPIC_OUTPUT_USD_PER_MTOK", 15.0)

# Export PDF/DOCX : coût forfaitaire fournisseur USD (hors API), facturé comme le reste avec marge MRU.
EXPORT_JOB_PROVIDER_USD = _f("EXPORT_JOB_PROVIDER_USD", 0.0005)


def usd_provider_to_billed_mru(usd_provider: float) -> float:
    return max(0.0, float(usd_provider)) * MRU_PER_USD * MARGIN_MULTIPLIER


def transcribe_aggregate_billed_mru(total_provider_usd: float) -> float:
    """
    Cout fournisseur total (Whisper + chat transcribe). Revente = USD × MRU_PER_USD × MULTIPLY.
    MULTIPLY = TRANSCRIBE_AI_RETAIL_MULTIPLIER si défini (>0), sinon CUSTOMER_MARGIN_MULTIPLIER (souvent 3).
    """
    raw_mu = os.getenv("TRANSCRIBE_AI_RETAIL_MULTIPLIER")
    if raw_mu is None or str(raw_mu).strip() == "":
        mu = MARGIN_MULTIPLIER
    else:
        try:
            mu = float(str(raw_mu).strip())
            if mu <= 0:
                mu = MARGIN_MULTIPLIER
        except ValueError:
            mu = MARGIN_MULTIPLIER
    return max(0.0, float(total_provider_usd)) * MRU_PER_USD * mu


def openai_transcribe_chat_provider_usd(prompt_tokens: int, completion_tokens: int) -> float:
    pi = max(0, int(prompt_tokens))
    co = max(0, int(completion_tokens))
    return (pi / 1_000_000.0) * OPENAI_TRANSCRIBE_CHAT_INPUT_USD_PER_MTOK + (
        co / 1_000_000.0
    ) * OPENAI_TRANSCRIBE_CHAT_OUTPUT_USD_PER_MTOK


def billed_mru_to_wallet_units_debit(billed_mru: float) -> int:
    """Débit : on arrondit au supérieur en unités portefeuille (ne pas vendre en dessous du tarif)."""
    if billed_mru <= 0:
        return 0
    return max(1, int(math.ceil(float(billed_mru) * MRU_WALLET_MICRO - 1e-12)))


def grant_mru_to_wallet_units(grant_mru: float) -> int:
    """Crédit manuel ou calculé : au moins 1 unité si montant MRU > 0."""
    if grant_mru <= 0:
        raise ValueError("grant_mru must be positive")
    u = int(round(float(grant_mru) * MRU_WALLET_MICRO))
    return max(1, u)


def wallet_units_to_mru_display(units: int) -> float:
    return round(float(units) / float(MRU_WALLET_MICRO), 4)


def estimate_tokens_from_chars(text: str) -> int:
    if not text or not text.strip():
        return 0
    return max(1, int(round(len(text) / 4)))


def whisper_provider_usd(duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    minutes = duration_seconds / 60.0
    return minutes * OPENAI_WHISPER_USD_PER_MINUTE


def whisper_billed(duration_seconds: float) -> tuple[float, float]:
    usd = whisper_provider_usd(duration_seconds)
    return usd, usd_provider_to_billed_mru(usd)


def claude_provider_usd(input_tokens: int, output_tokens: int) -> float:
    return (max(0, input_tokens) / 1_000_000.0) * ANTHROPIC_INPUT_USD_PER_MTOK + (
        max(0, output_tokens) / 1_000_000.0
    ) * ANTHROPIC_OUTPUT_USD_PER_MTOK


def claude_billed(input_tokens: int, output_tokens: int) -> tuple[float, float]:
    usd = claude_provider_usd(input_tokens, output_tokens)
    return usd, usd_provider_to_billed_mru(usd)


def export_job_billed() -> tuple[float, float]:
    u = EXPORT_JOB_PROVIDER_USD
    return float(u), usd_provider_to_billed_mru(u)
