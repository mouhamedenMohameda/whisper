"""Pré-vérification audio : détecte les fichiers trop bas en volume avant de payer Whisper.

Idée : avant de créer un ``TranscriptionJob`` (donc avant le débit MRU et avant l'attente de
~10 min), on mesure rapidement le volume moyen et le pic via ``ffmpeg -af volumedetect``. Si le
fichier est sous les seuils, on rejette immédiatement avec un message d'aide pour l'utilisateur.

Pourquoi c'est le bon endroit pour gate :
  - **Cheap** : 1-2 secondes (vs 10 min de transcription)
  - **Zero risque** : on ne touche pas à l'audio, juste une mesure
  - **Reversible** : env vars pour ajuster les seuils ou désactiver totalement
  - **Utile** : empêche les boucles d'hallucinations Whisper sur audio bruité/silencieux

Ce qu'on mesure :
  - ``mean_volume`` : niveau RMS moyen. Sous -32 dB = très bas (un cours normal est ~-20 à -25 dB)
  - ``max_volume`` : pic max. Si le pic ne dépasse pas -15 dB, l'audio est globalement trop faible

Limite : on analyse les **60 premières secondes** pour aller vite. Si l'audio est correct en
intro puis se dégrade, on ne détectera pas. Mais c'est un cas rare dans la pratique.

Désactivable via ``TRANSCRIBE_AUDIO_PREVALIDATION_ENABLED=false``.
Seuils ajustables via ``TRANSCRIBE_AUDIO_MIN_MEAN_DB`` et ``TRANSCRIBE_AUDIO_MIN_PEAK_DB``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AudioAnalysis:
    """Résultat d'une analyse ffprobe/volumedetect."""

    mean_volume_db: Optional[float]
    max_volume_db: Optional[float]
    is_acceptable: bool
    # Raison lisible côté user (FR). ``None`` si acceptable ou analyse impossible.
    reason: Optional[str]
    # ``True`` si on a vraiment pu mesurer. ``False`` = ffmpeg absent / format inconnu / etc.
    # Dans ce cas, ``is_acceptable=True`` par défaut (on ne bloque pas un user à cause d'un souci infra).
    measured: bool


_MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", re.IGNORECASE)
_MAX_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", re.IGNORECASE)


def is_enabled() -> bool:
    return (os.getenv("TRANSCRIBE_AUDIO_PREVALIDATION_ENABLED", "true").strip().lower()
            in ("1", "true", "yes", "on"))


def _thresholds() -> tuple[float, float]:
    """Retourne ``(mean_min_db, max_min_db)``.

    Calibrage par défaut :
      - mean_min_db = -32 : cours en salle normal est ~-20 à -28 dB ; -32 c'est "très très bas"
      - max_min_db = -15 : un cours normal a des pics à -3 à -6 dB ; -15 c'est "aucune intensité"

    Volontairement permissif pour ne pas rejeter des audios légèrement faibles mais utilisables.
    Si tu vois passer des audios qui hallucinent malgré tout, monte ``mean_min_db`` (ex: -28).
    """
    try:
        mean_min = float(os.getenv("TRANSCRIBE_AUDIO_MIN_MEAN_DB", "-32"))
    except ValueError:
        mean_min = -32.0
    try:
        max_min = float(os.getenv("TRANSCRIBE_AUDIO_MIN_PEAK_DB", "-15"))
    except ValueError:
        max_min = -15.0
    return mean_min, max_min


def analyze_audio(file_path: Path, *, sample_seconds: int = 60) -> AudioAnalysis:
    """Mesure le volume des ``sample_seconds`` premières secondes de l'audio.

    En cas d'erreur infra (ffmpeg absent, fichier corrompu, codec exotique), on retourne
    ``is_acceptable=True`` + ``measured=False`` — on n'a pas le droit de bloquer un user
    à cause d'un souci côté serveur.
    """
    if not is_enabled():
        return AudioAnalysis(None, None, True, None, measured=False)

    file_path = Path(file_path)
    if not file_path.exists():
        logger.info("Audio prevalidation: fichier introuvable %s", file_path)
        return AudioAnalysis(None, None, True, None, measured=False)

    ffmpeg_exe = shutil.which(os.getenv("FFMPEG_BINARY", "ffmpeg"))
    if not ffmpeg_exe:
        logger.warning("Audio prevalidation: ffmpeg introuvable — skip")
        return AudioAnalysis(None, None, True, None, measured=False)

    # ``volumedetect`` écrit dans stderr. ``-f null -`` jette la sortie audio (on veut juste les stats).
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-nostdin",
        "-t", str(int(max(5, sample_seconds))),
        "-i", str(file_path),
        "-af", "volumedetect",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            timeout=60,
            capture_output=True,
            text=True,
            check=False,  # volumedetect retourne souvent != 0 même en succès
        )
    except subprocess.TimeoutExpired:
        logger.warning("Audio prevalidation: ffmpeg timeout — skip")
        return AudioAnalysis(None, None, True, None, measured=False)
    except Exception:
        logger.exception("Audio prevalidation: ffmpeg run failed — skip")
        return AudioAnalysis(None, None, True, None, measured=False)

    stderr = result.stderr or ""
    mean_match = _MEAN_RE.search(stderr)
    max_match = _MAX_RE.search(stderr)
    mean_db: Optional[float] = float(mean_match.group(1)) if mean_match else None
    max_db: Optional[float] = float(max_match.group(1)) if max_match else None

    if mean_db is None and max_db is None:
        # ffmpeg n'a rien sorti d'exploitable (codec étrange, fichier corrompu...).
        logger.info("Audio prevalidation: ffmpeg n'a pas produit de stats volume — skip")
        return AudioAnalysis(None, None, True, None, measured=False)

    mean_min, max_min = _thresholds()
    reasons: list[str] = []
    if mean_db is not None and mean_db < mean_min:
        reasons.append(f"volume moyen trop bas ({mean_db:.1f} dB, seuil {mean_min:.0f} dB)")
    if max_db is not None and max_db < max_min:
        reasons.append(f"pic max trop faible ({max_db:.1f} dB, seuil {max_min:.0f} dB)")

    is_acceptable = len(reasons) == 0
    reason = " ; ".join(reasons) if reasons else None

    logger.info(
        "Audio prevalidation: mean=%s dB max=%s dB acceptable=%s reason=%s",
        f"{mean_db:.1f}" if mean_db is not None else "?",
        f"{max_db:.1f}" if max_db is not None else "?",
        is_acceptable, reason,
    )
    return AudioAnalysis(mean_db, max_db, is_acceptable, reason, measured=True)
