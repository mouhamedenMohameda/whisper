"""Grille tarifaire transcription (MRU/heure audio) + paliers fidélité — source unique pour l’extension.

Les champs ``label_fr`` / ``label_ar`` sont **libellés marketing** (forces du modèle, sans noms commerciaux
obligatoires) ; les identifiants techniques restent ``id`` et ``api_model``.

Pour ajouter un modèle
----------------------
1. Ajouter une entrée dans ``RETAIL_MODELS`` (clé = identifiant API stocké en base, max 24 caractères).
2. Renseigner ``provider`` (``openai`` | ``groq`` | ``local``), ``api_model`` (nom côté API audio),
   les trois prix MRU/h (``mru_nouveau``, ``mru_regular``, ``mru_loyal``) et optionnellement ``cost_mru_per_hour``.
3. Si besoin d’alias utilisateur (ex. ancien ``openai``), les ajouter dans ``ENGINE_ALIASES``.
4. Vérifier qu’une clé API adaptée est présente sur le serveur (``OPENAI_API_KEY`` ou ``GROQ_API_KEY``).

Les paliers sont dérivés des heures cumulées **par modèle** dans ``user_transcription_model_hours``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# Seuils en heures cumulées : [0, H1) nouveau, [H1, H2) régulier, [H2, +∞) fidèle.
LOYALTY_TIER_BOUNDARY_HOURS_1 = 1.0
LOYALTY_TIER_BOUNDARY_HOURS_2 = 5.0

LoyaltyTier = Literal["nouveau", "regular", "loyal"]
TranscriptionProvider = Literal["openai", "groq", "local"]


@dataclass(frozen=True)
class TranscriptionRetailModel:
    """Spécification d’un modèle de transcription facturable au palier."""

    id: str
    provider: TranscriptionProvider
    """Moteur d’inférence audio (clé API requise : OpenAI ou Groq selon le cas)."""

    api_model: str
    """Nom du modèle passé à l’endpoint ``audio.transcriptions`` (OpenAI ou compatible Groq)."""

    mru_nouveau: float
    mru_regular: float
    mru_loyal: float
    cost_mru_per_hour: Optional[float] = None
    """Coût d’achat indicatif (MRU/h) — pour reporting / admin, non utilisé pour débiter l’étudiant."""

    label_fr: str = ""
    label_ar: str = ""


# Clé canonique = valeur persistée dans ``transcription_jobs.transcription_engine`` et formulaires.
RETAIL_MODELS: dict[str, TranscriptionRetailModel] = {
    "whisper-large-v3-turbo": TranscriptionRetailModel(
        id="whisper-large-v3-turbo",
        provider="groq",
        api_model="whisper-large-v3-turbo",
        mru_nouveau=5.0,
        mru_regular=3.5,
        mru_loyal=2.8,
        cost_mru_per_hour=1.60,
        label_fr="Réponse express",
        label_ar="سريع التسليم",
    ),
    "whisper-large-v3": TranscriptionRetailModel(
        id="whisper-large-v3",
        provider="groq",
        api_model="whisper-large-v3",
        mru_nouveau=10.0,
        mru_regular=8.0,
        mru_loyal=7.5,
        cost_mru_per_hour=4.44,
        label_fr="Profil équilibré",
        label_ar="متوازن الجودة",
    ),
    "gpt-4o-mini-transcribe": TranscriptionRetailModel(
        id="gpt-4o-mini-transcribe",
        provider="openai",
        api_model="gpt-4o-mini-transcribe",
        mru_nouveau=15.0,
        mru_regular=13.0,
        mru_loyal=12.0,
        cost_mru_per_hour=7.20,
        label_fr="Transcription affinée",
        label_ar="نسخ دقيق",
    ),
    "whisper-1": TranscriptionRetailModel(
        id="whisper-1",
        provider="openai",
        api_model="whisper-1",
        mru_nouveau=30.0,
        mru_regular=25.0,
        mru_loyal=23.0,
        cost_mru_per_hour=14.40,
        label_fr="Excellence audio",
        label_ar="جودة فائقة",
    ),
    "local": TranscriptionRetailModel(
        id="local",
        provider="local",
        api_model="local",
        mru_nouveau=2.0,
        mru_regular=1.5,
        mru_loyal=1.0,
        cost_mru_per_hour=0.0,
        label_fr="Atelier privé (économique)",
        label_ar="ورشة خاصة (اقتصادي)",
    ),
}

# Entrées utilisateur / legacy → id canonique dans ``RETAIL_MODELS``.
ENGINE_ALIASES: dict[str, str] = {
    "openai": "whisper-1",
    "whisper": "whisper-1",
    "cloud": "whisper-1",
    "turbo": "whisper-large-v3-turbo",
    "large": "whisper-large-v3",
    "large-v3": "whisper-large-v3",
    "gpt4o_mini": "gpt-4o-mini-transcribe",
    "gpt4o-mini-transcribe": "gpt-4o-mini-transcribe",
    "local_whisper": "local",
    "offline": "local",
    "cpu": "local",
}


def canonical_transcription_model_id(raw: Optional[str]) -> str:
    s = (raw or "whisper-1").strip().lower()
    if not s:
        s = "whisper-1"
    return ENGINE_ALIASES.get(s, s)


def get_retail_model(model_id: str) -> TranscriptionRetailModel:
    mid = canonical_transcription_model_id(model_id)
    spec = RETAIL_MODELS.get(mid)
    if spec is None:
        raise KeyError(mid)
    return spec


def loyalty_tier_from_lifetime_hours(hours_lifetime: float) -> LoyaltyTier:
    """Palier fidélité par moteur (heures cumulées sur ce modèle uniquement).

    Interprétation grille « 0 → 1 h » : le palier **Nouveau** inclut jusqu’à **1 h réalisée incluse**.
    **Régulier** : au-delà de 1 h jusqu’à strictement moins de 5 h. **Fidèle** : à partir de 5 h.
    """
    h = max(0.0, float(hours_lifetime))
    if h <= LOYALTY_TIER_BOUNDARY_HOURS_1:
        return "nouveau"
    if h < LOYALTY_TIER_BOUNDARY_HOURS_2:
        return "regular"
    return "loyal"


def mru_per_hour_for_tier(spec: TranscriptionRetailModel, tier: LoyaltyTier) -> float:
    if tier == "nouveau":
        return max(0.0, float(spec.mru_nouveau))
    if tier == "regular":
        return max(0.0, float(spec.mru_regular))
    return max(0.0, float(spec.mru_loyal))


def max_mru_per_hour_for_model(spec: TranscriptionRetailModel) -> float:
    """Plafond tarifaire du modèle (palier le plus cher) — réserves portefeuille."""
    return max(spec.mru_nouveau, spec.mru_regular, spec.mru_loyal)


def audio_hours_from_duration_seconds(duration_sec: float) -> float:
    return max(0.0, float(duration_sec)) / 3600.0


def retail_mru_for_audio(
    *,
    spec: TranscriptionRetailModel,
    lifetime_hours_before_job: float,
    duration_seconds: float,
) -> tuple[float, LoyaltyTier, float, float]:
    """
    Retourne (mru_audio_facturé, palier_appliqué, heures_audio_facturées, tarif_mru_h_appliqué).
    Le palier est basé sur les heures cumulées **avant** cette transcription.
    """
    tier = loyalty_tier_from_lifetime_hours(lifetime_hours_before_job)
    rate = mru_per_hour_for_tier(spec, tier)
    bill_hours = audio_hours_from_duration_seconds(duration_seconds)
    return float(bill_hours * rate), tier, bill_hours, float(rate)


def public_catalog_entries() -> list[dict]:
    """Sérialisation stable pour ``/api/credits/transcription-retail``."""
    out: list[dict] = []
    for mid in sorted(RETAIL_MODELS.keys(), key=lambda x: (RETAIL_MODELS[x].provider != "local", x)):
        s = RETAIL_MODELS[mid]
        out.append(
            {
                "id": s.id,
                "provider": s.provider,
                "label_fr": s.label_fr or s.id,
                "label_ar": s.label_ar or s.label_fr or s.id,
                "mru_per_hour": {
                    "nouveau": s.mru_nouveau,
                    "regular": s.mru_regular,
                    "loyal": s.mru_loyal,
                },
                "cost_mru_per_hour": s.cost_mru_per_hour,
            },
        )
    return out


def loyalty_meta_for_hours(hours_lifetime: float) -> dict:
    """Infobulle palier + progression vers le suivant."""
    h = max(0.0, float(hours_lifetime))
    tier = loyalty_tier_from_lifetime_hours(h)
    if tier == "nouveau":
        next_threshold = LOYALTY_TIER_BOUNDARY_HOURS_1
        label = "nouveau"
        hours_into = h
        span = LOYALTY_TIER_BOUNDARY_HOURS_1
    elif tier == "regular":
        next_threshold = LOYALTY_TIER_BOUNDARY_HOURS_2
        label = "regular"
        hours_into = h - LOYALTY_TIER_BOUNDARY_HOURS_1
        span = LOYALTY_TIER_BOUNDARY_HOURS_2 - LOYALTY_TIER_BOUNDARY_HOURS_1
    else:
        next_threshold = None
        label = "loyal"
        hours_into = h - LOYALTY_TIER_BOUNDARY_HOURS_2
        span = None

    hours_until_next: Optional[float] = None
    progress_to_next: Optional[float] = None
    if next_threshold is not None:
        hours_until_next = max(0.0, next_threshold - h)
        if span and span > 0:
            progress_to_next = min(1.0, max(0.0, hours_into / span))

    return {
        "tier": tier,
        "tier_label": label,
        "hours_lifetime": round(h, 4),
        "next_tier_threshold_hours": next_threshold,
        "hours_until_next_tier": None if hours_until_next is None else round(hours_until_next, 4),
        "progress_fraction_within_tier": None if progress_to_next is None else round(progress_to_next, 4),
    }
