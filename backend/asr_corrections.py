"""Corrections déterministes post-Whisper via dictionnaire JSON + regex word-boundary.

Approche choisie après 3 échecs successifs de techniques sophistiquées (ffmpeg filters,
Whisper initial_prompt, LLM post-cleanup) : on n'utilise **aucun modèle ML** pour ce nettoyage.
Juste du remplacement texte basé sur un dictionnaire que l'utilisateur étend lui-même au fil
des erreurs récurrentes qu'il observe.

Avantages :
  - Aucun risque de boucle / hallucination (substitution déterministe)
  - Aucun appel LLM (zéro coût, zéro latence)
  - Transparence : chaque correction est loggée
  - L'user étend lui-même le dictionnaire (un edit du JSON, restart pm2)

Limites :
  - Ne corrige que les patterns connus à l'avance
  - Pas de contexte (`j'essuie` est remplacé par `je suis` même dans "j'essuie la table")
    → pour minimiser ce risque, on évite les substitutions ambiguës dans le JSON par défaut

Fichier JSON : ``backend/asr_corrections.json`` (créé au premier lancement si absent).
Format : ``{"motif_a_corriger": "remplacement", ...}``. Le motif est matché en **insensible
à la casse** et avec **frontières de mots** automatiques (pas de remplacement partiel à
l'intérieur d'un autre mot — ex. "atomie" ne match pas dans "anatomie").

Désactivable via ``TRANSCRIBE_ASR_CORRECTIONS_ENABLED=false``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_CORRECTIONS_PATH = Path(__file__).resolve().parent / "asr_corrections.json"

# Lazy : on charge à la première utilisation puis on garde en cache + on watch le mtime pour
# recharger si l'user édite le JSON sans restart.
_LOCK = threading.Lock()
_CACHE: dict[str, str] = {}
_CACHE_MTIME: float = 0.0
_CACHE_PATTERN: Optional[re.Pattern[str]] = None


# Corrections par défaut, écrites dans le JSON à la première utilisation si le fichier est absent.
# Choisies pour être *sûres* : pas de substitution qui change le sens dans le doute.
_DEFAULTS: dict[str, str] = {
    # Hallucinations Whisper classiques (générique YouTube) — à supprimer purement
    "Sous-titres réalisés par la communauté d'Amara.org": "",
    "Sous-titres réalisés pour la communauté d'Amara.org": "",
    "Sous-titres réalisés à partir des sous-titres de la BBC": "",
    "Merci d'avoir regardé cette vidéo": "",
    "Amara.org": "",
    "❤️ par SousTitreur.com": "",
    # Mots tronqués typiques sur vocabulaire médical
    "atomie": "anatomie",
    "moragie": "hémorragie",
    "moragique": "hémorragique",
    "noplasie": "néoplasie",
    "fection": "infection",
    "flammation": "inflammation",
    # Anglicismes audio courants à corriger en français
    "patho": "pathologie",
}


def is_enabled() -> bool:
    return (os.getenv("TRANSCRIBE_ASR_CORRECTIONS_ENABLED", "true").strip().lower()
            in ("1", "true", "yes", "on"))


def _corrections_path() -> Path:
    """Permet de surcharger le chemin via env (utile pour tests)."""
    override = (os.getenv("TRANSCRIBE_ASR_CORRECTIONS_PATH") or "").strip()
    if override:
        return Path(override)
    return _DEFAULT_CORRECTIONS_PATH


def _ensure_default_file(path: Path) -> None:
    """Crée le fichier JSON avec les corrections par défaut s'il n'existe pas."""
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(_DEFAULTS, f, ensure_ascii=False, indent=2, sort_keys=True)
        logger.info("ASR corrections: fichier par défaut créé à %s (%d entrées)", path, len(_DEFAULTS))
    except OSError:
        logger.warning("ASR corrections: impossible d'écrire %s — défauts en mémoire uniquement", path,
                       exc_info=True)


def _load_corrections() -> tuple[dict[str, str], Optional[re.Pattern[str]]]:
    """Charge le JSON et compile une regex unique qui matche n'importe lequel des motifs.

    On compile une seule grosse regex (alternance) parce que ``re.sub`` est ~50× plus rapide
    qu'une boucle de N appels distincts sur des transcripts longs (50k+ chars).
    """
    global _CACHE, _CACHE_MTIME, _CACHE_PATTERN
    path = _corrections_path()
    _ensure_default_file(path)

    try:
        mtime = path.stat().st_mtime
    except OSError:
        # Fichier disparu ou inaccessible — on fallback sur les défauts en mémoire.
        if _CACHE:
            return _CACHE, _CACHE_PATTERN
        _CACHE = dict(_DEFAULTS)
        _CACHE_PATTERN = _compile_pattern(_CACHE)
        _CACHE_MTIME = 0.0
        return _CACHE, _CACHE_PATTERN

    with _LOCK:
        # Cache hit : mtime inchangé → on retourne tel quel.
        if _CACHE_PATTERN is not None and mtime == _CACHE_MTIME:
            return _CACHE, _CACHE_PATTERN
        # Reload : fichier a changé (ou jamais chargé).
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("JSON racine doit être un objet {motif: remplacement}")
            data: dict[str, str] = {}
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str) and k:
                    data[k] = v
            _CACHE = data
            _CACHE_PATTERN = _compile_pattern(data)
            _CACHE_MTIME = mtime
            logger.info("ASR corrections: %d entrées chargées depuis %s", len(data), path)
        except Exception:
            logger.exception("ASR corrections: JSON invalide à %s — défauts en mémoire", path)
            _CACHE = dict(_DEFAULTS)
            _CACHE_PATTERN = _compile_pattern(_CACHE)
            _CACHE_MTIME = mtime  # évite de retry à chaque appel sur un JSON cassé
    return _CACHE, _CACHE_PATTERN


def _compile_pattern(data: dict[str, str]) -> Optional[re.Pattern[str]]:
    """Compile une regex d'alternance avec word boundaries, triée par longueur décroissante.

    Tri longueur DESC : essentiel pour que "Sous-titres réalisés ... Amara.org" soit matché
    avant le sous-motif "Amara.org" (sinon le sous-motif consomme le texte en premier).
    """
    if not data:
        return None
    keys = sorted(data.keys(), key=len, reverse=True)
    alternation = "|".join(re.escape(k) for k in keys)
    # \b côté début/fin : ne matche pas au milieu d'un mot (ex. "atomie" ne touche pas "anatomie").
    # Pour les motifs qui commencent/finissent par un non-word char (espace, ponctuation), \b
    # peut être inadéquat — on accepte cette limite, c'est rare et pas grave.
    pattern = re.compile(r"\b(?:" + alternation + r")\b", re.IGNORECASE)
    return pattern


def apply_corrections(text: str) -> tuple[str, int]:
    """Applique toutes les corrections du dictionnaire. Retourne ``(texte_corrigé, nb_remplacements)``.

    Jamais d'exception : tout échec → retour de l'original avec ``0`` substitutions.
    """
    if not is_enabled() or not text:
        return text, 0
    try:
        data, pattern = _load_corrections()
    except Exception:
        logger.exception("ASR corrections: chargement échoué — pas de correction appliquée")
        return text, 0
    if not pattern:
        return text, 0

    count = 0

    def _repl(m: re.Match[str]) -> str:
        nonlocal count
        match_text = m.group(0)
        # Lookup case-insensitive : on recherche la clé qui match (peu importe la casse).
        for key, value in data.items():
            if key.lower() == match_text.lower():
                count += 1
                return value
        return match_text  # ne devrait jamais arriver

    result = pattern.sub(_repl, text)

    # Nettoyage : si on a supprimé des hallucinations (remplacement vide), on peut se retrouver
    # avec des doubles espaces / espaces avant ponctuation. On normalise.
    if count > 0:
        result = re.sub(r"[ \t]+", " ", result)
        result = re.sub(r"\s+([,.!?;:])", r"\1", result)
        result = result.strip()

    return result, count
