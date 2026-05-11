"""Tarification côté utilisateur : USD fournisseur → MRU facturés avec marge et taux.

1 USD ≈ MRU_PER_USD (défaut 40). Prix client (MRU) = USD_fournisseur × MRU_PER_USD × MARGIN.

**Schéma portefeuille (`users.credit_balance`)** — Le solde en base est un **entier d’unités** ; on ne
peut pas stocker une fraction d’unité sans changer le schéma (type / écritures fractionnaires) ou
sans monter **MRU_WALLET_MICRO** dans l’environnement (``.env``) pour réduire l’erreur de
quantification. Dans ce cadre, **demi au plus proche** (``Decimal`` + ``ROUND_HALF_UP``) est le
compromis le plus neutre : ni « toujours au-dessus » comme ``ceil``, ni biais client comme
``floor``. Pour coller encore plus au « réel » sur des montants MRU minuscules, augmenter
``MRU_WALLET_MICRO`` (ex. ``100000`` au lieu de ``10000``).
"""

from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _nonneg(v: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return max(0.0, f)


def _wallet_micro() -> int:
    """Nombre d’unités entières par 1 MRU affiché (quantification du portefeuille)."""
    raw = os.getenv("MRU_WALLET_MICRO")
    if raw is None or raw.strip() == "":
        # Par défaut on garde une précision suffisante pour les micro-coûts (ex. 0.98304 MRU).
        return 1_000_000
    try:
        v = int(raw)
        return max(100, min(v, 1_000_000_000))
    except ValueError:
        return 1_000_000


MRU_WALLET_MICRO = _wallet_micro()

# Billable multiplier (marge client sur le coût USD fournisseur)
# Défauts « bas et logiques » : faible marge au-dessus du coût fournisseur (surchargeable en prod).
MRU_PER_USD = _nonneg(_f("MRU_PER_USD", 40.0))
MARGIN_MULTIPLIER = _nonneg(_f("CUSTOMER_MARGIN_MULTIPLIER", 1.28))

OPENAI_WHISPER_USD_PER_MINUTE = _f("OPENAI_WHISPER_USD_PER_MINUTE", 0.006)
LOCAL_WHISPER_USD_PER_MINUTE = _f("LOCAL_WHISPER_USD_PER_MINUTE", 0.002)

# GPT (chat completions) utilisé sur /transcribe : polish + vue langues — prix fournisseur au million de tokens (ajuste selon tarif réel du modèle).
OPENAI_TRANSCRIBE_CHAT_INPUT_USD_PER_MTOK = _f("OPENAI_TRANSCRIBE_CHAT_INPUT_USD_PER_MTOK", 0.15)
OPENAI_TRANSCRIBE_CHAT_OUTPUT_USD_PER_MTOK = _f("OPENAI_TRANSCRIBE_CHAT_OUTPUT_USD_PER_MTOK", 0.60)

# Groq (analyse sujet + résumé sur /transcribe) — ajuste selon le modèle / tarif réel.
GROQ_INPUT_USD_PER_MTOK = _nonneg(_f("GROQ_INPUT_USD_PER_MTOK", 0.05))
GROQ_OUTPUT_USD_PER_MTOK = _nonneg(_f("GROQ_OUTPUT_USD_PER_MTOK", 0.08))

# Groq — assistant chat : tarifs fournisseur au million (résumé 8B vs chat principal 20B par défaut).
GROQ_CHAT_SUMMARY_INPUT_USD_PER_MTOK = _nonneg(_f("GROQ_CHAT_SUMMARY_INPUT_USD_PER_MTOK", 0.05))
GROQ_CHAT_SUMMARY_OUTPUT_USD_PER_MTOK = _nonneg(_f("GROQ_CHAT_SUMMARY_OUTPUT_USD_PER_MTOK", 0.08))
GROQ_CHAT_MAIN_INPUT_USD_PER_MTOK = _nonneg(_f("GROQ_CHAT_MAIN_INPUT_USD_PER_MTOK", 0.075))
GROQ_CHAT_MAIN_OUTPUT_USD_PER_MTOK = _nonneg(_f("GROQ_CHAT_MAIN_OUTPUT_USD_PER_MTOK", 0.30))

# Marge client dédiée au chat (défaut ×2 sur le coût fournisseur USD avant conversion MRU).
CHAT_CUSTOMER_MARGIN_MULTIPLIER = _nonneg(_f("CHAT_CUSTOMER_MARGIN_MULTIPLIER", 2.0))

ANTHROPIC_INPUT_USD_PER_MTOK = _f("ANTHROPIC_INPUT_USD_PER_MTOK", 3.0)
ANTHROPIC_OUTPUT_USD_PER_MTOK = _f("ANTHROPIC_OUTPUT_USD_PER_MTOK", 15.0)

# Export PDF/DOCX : coût forfaitaire fournisseur USD (hors API), facturé comme le reste avec marge MRU.
EXPORT_JOB_PROVIDER_USD = _f("EXPORT_JOB_PROVIDER_USD", 0.0005)


def usd_provider_to_billed_mru(usd_provider: float) -> float:
    return _nonneg(usd_provider) * MRU_PER_USD * MARGIN_MULTIPLIER


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


def groq_chat_provider_usd(prompt_tokens: int, completion_tokens: int) -> float:
    pi = max(0, int(prompt_tokens))
    co = max(0, int(completion_tokens))
    return (pi / 1_000_000.0) * GROQ_INPUT_USD_PER_MTOK + (co / 1_000_000.0) * GROQ_OUTPUT_USD_PER_MTOK


def groq_billed(input_tokens: int, output_tokens: int) -> tuple[float, float]:
    usd = groq_chat_provider_usd(input_tokens, output_tokens)
    return usd, usd_provider_to_billed_mru(usd)


def groq_chat_summary_provider_usd(prompt_tokens: int, completion_tokens: int) -> float:
    pi = max(0, int(prompt_tokens))
    co = max(0, int(completion_tokens))
    return (pi / 1_000_000.0) * GROQ_CHAT_SUMMARY_INPUT_USD_PER_MTOK + (
        co / 1_000_000.0
    ) * GROQ_CHAT_SUMMARY_OUTPUT_USD_PER_MTOK


def groq_chat_main_provider_usd(prompt_tokens: int, completion_tokens: int) -> float:
    pi = max(0, int(prompt_tokens))
    co = max(0, int(completion_tokens))
    return (pi / 1_000_000.0) * GROQ_CHAT_MAIN_INPUT_USD_PER_MTOK + (co / 1_000_000.0) * GROQ_CHAT_MAIN_OUTPUT_USD_PER_MTOK


def chat_assistant_billed_mru(
    summary_prompt_tokens: int,
    summary_completion_tokens: int,
    main_prompt_tokens: int,
    main_completion_tokens: int,
) -> tuple[float, float]:
    """Retourne (usd_fournisseur_total, mru_facturé) pour un tour assistant (résumé optionnel + chat)."""
    usd_s = groq_chat_summary_provider_usd(summary_prompt_tokens, summary_completion_tokens)
    usd_m = groq_chat_main_provider_usd(main_prompt_tokens, main_completion_tokens)
    total_usd = _nonneg(usd_s) + _nonneg(usd_m)
    mu = _nonneg(CHAT_CUSTOMER_MARGIN_MULTIPLIER)
    mru = _nonneg(total_usd) * MRU_PER_USD * mu
    return float(total_usd), float(mru)


def _mru_to_wallet_units_exact(billed_mru: float) -> int:
    """MRU → unités portefeuille : demi au plus proche (évite sur-facturation ``ceil`` et sous-facturation biaisée)."""
    if billed_mru <= 0:
        return 0
    # Ne pas repasser par float() (perte de précision sur de petits montants).
    d = Decimal(str(billed_mru)) * Decimal(int(MRU_WALLET_MICRO))
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def billed_mru_to_wallet_units_debit(billed_mru: float) -> int:
    """Débit : unités = MRU facturé × micro, arrondi demi au plus proche (coût réel dans la précision du portefeuille)."""
    return _mru_to_wallet_units_exact(billed_mru)


def grant_mru_to_wallet_units(grant_mru: float) -> int:
    """Crédit manuel ou calculé : même règle d’arrondi que le débit (symétrie)."""
    if grant_mru <= 0:
        raise ValueError("grant_mru must be positive")
    u = _mru_to_wallet_units_exact(grant_mru)
    if u <= 0:
        raise ValueError("grant_mru trop petit pour la précision du portefeuille (MRU_WALLET_MICRO)")
    return u


def mru_signed_to_wallet_units_delta(mru: float) -> int:
    """Delta d’unités portefeuille : MRU > 0 crédit, MRU < 0 retrait, même arrondi demi."""
    v = float(mru)
    if v > 0:
        return grant_mru_to_wallet_units(v)
    if v < 0:
        u = _mru_to_wallet_units_exact(abs(v))
        if u <= 0:
            raise ValueError("retrait MRU trop petit pour la précision du portefeuille")
        return -u
    raise ValueError("mru must be non-zero")


def wallet_units_to_mru_display(units: int) -> float:
    # Aligné sur la précision micro du portefeuille (par défaut 1e-6 MRU).
    return round(float(units) / float(MRU_WALLET_MICRO), 6)


def estimate_tokens_from_chars(text: str) -> int:
    if not text or not text.strip():
        return 0
    return max(1, int(round(len(text) / 4)))


def whisper_provider_usd(duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    minutes = duration_seconds / 60.0
    return minutes * OPENAI_WHISPER_USD_PER_MINUTE


def local_whisper_provider_usd(duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    minutes = duration_seconds / 60.0
    return minutes * LOCAL_WHISPER_USD_PER_MINUTE


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
