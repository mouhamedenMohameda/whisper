"""Pré-traitement audio par VAD (Voice Activity Detection) pour booster la qualité ASR.

Pourquoi : sur un long enregistrement (cours 1h+), les longs silences sont la principale cause
d'hallucinations Whisper (il invente du texte aléatoire pendant les blancs). En compressant les
silences > N secondes, on :
  1. Élimine les sources d'hallucinations
  2. Raccourcit l'audio (souvent -30 à -50 %) → transcription 2× plus rapide, moins de coût API
  3. Améliore le rapport signal/bruit moyen (la part "parole utile" remonte)

Implémentation : Silero VAD (modèle PyTorch ~10 MB, déjà compatible CPU). Pas de GPU requis.
Le module est **lazy** : modèle chargé au premier appel uniquement.

Si Silero n'est pas installable / charge mal, on retourne ``None`` et l'appelant utilise
l'audio non-traité. Aucun crash possible — c'est un *enhancement*, pas une dépendance critique.

Variables d'environnement :
  TRANSCRIBE_ENABLE_VAD_COMPRESSION  défaut ``true`` — désactiver pour A/B testing
  TRANSCRIBE_VAD_THRESHOLD           défaut 0.5 (Silero confidence 0..1, plus haut = plus strict)
  TRANSCRIBE_VAD_MIN_SPEECH_MS       défaut 250 — segments parlés plus courts ignorés
  TRANSCRIBE_VAD_MIN_SILENCE_MS      défaut 800 — silences plus courts conservés (naturel)
  TRANSCRIBE_VAD_PAD_MS              défaut 200 — marge avant/après chaque segment parlé (anti-troncature)
  TRANSCRIBE_VAD_MAX_SEGMENTS        défaut 800 — au-delà, on bypasse (filter ffmpeg trop long)

Installation :
  pip install silero-vad
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Singleton + lock pour ne charger le modèle qu'une seule fois par process.
_VAD_MODEL: Any = None
_VAD_LOAD_LOCK = threading.Lock()
_VAD_LOAD_FAILED = False  # évite de retry l'import en boucle si silero-vad pas installé


def _env_truthy(name: str, default: bool = False) -> bool:
    val = os.getenv(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def is_vad_enabled() -> bool:
    return _env_truthy("TRANSCRIBE_ENABLE_VAD_COMPRESSION", True)


def _load_silero() -> Optional[Any]:
    """Charge Silero VAD (lazy, thread-safe). Retourne ``None`` si indisponible."""
    global _VAD_MODEL, _VAD_LOAD_FAILED
    if _VAD_MODEL is not None:
        return _VAD_MODEL
    if _VAD_LOAD_FAILED:
        return None
    with _VAD_LOAD_LOCK:
        if _VAD_MODEL is not None:
            return _VAD_MODEL
        if _VAD_LOAD_FAILED:
            return None
        try:
            import torch  # noqa: F401
            from silero_vad import load_silero_vad

            # CPU only — sur 1h d'audio le VAD prend ~10s, pas besoin de GPU.
            _VAD_MODEL = load_silero_vad()
            logger.info("Silero VAD chargé en mémoire (singleton process).")
            return _VAD_MODEL
        except Exception:
            _VAD_LOAD_FAILED = True
            logger.warning(
                "Silero VAD indisponible (pip install silero-vad) — VAD compression désactivée.",
                exc_info=True,
            )
            return None


def vad_compress(input_path: Path, output_path: Path) -> Optional[Path]:
    """Compresse les silences > seuil dans ``input_path`` et écrit le résultat dans ``output_path``.

    Retourne ``output_path`` si la compression a réussi, ``None`` sinon (l'appelant doit utiliser
    l'audio d'origine — c'est un graceful fallback, **jamais** une exception).

    Pré-conditions sur l'input :
      - Pas obligatoire en 16 kHz mono — Silero lit n'importe quel format que ffmpeg accepte,
        mais on convertit en interne via ``read_audio()``. Le résultat sortant est en 16 kHz mono.
    """
    if not is_vad_enabled():
        return None

    input_path = Path(input_path)
    if not input_path.exists():
        logger.warning("VAD: input introuvable %s — skip", input_path)
        return None

    model = _load_silero()
    if model is None:
        return None

    try:
        from silero_vad import get_speech_timestamps, read_audio
    except ImportError:
        logger.warning("silero-vad introuvable à l'import tardif — skip")
        return None

    try:
        wav = read_audio(str(input_path), sampling_rate=16000)
    except Exception:
        logger.exception("VAD: read_audio a échoué pour %s — fallback", input_path)
        return None

    total_input_sec = len(wav) / 16000.0
    if total_input_sec < 5.0:
        # Audio trop court (<5s) : pas de gain à attendre, et risque de tout dégager si VAD foire.
        logger.info("VAD: audio %.1fs < 5s — skip", total_input_sec)
        return None

    try:
        threshold = float(os.getenv("TRANSCRIBE_VAD_THRESHOLD", "0.5"))
        min_speech_ms = int(os.getenv("TRANSCRIBE_VAD_MIN_SPEECH_MS", "250"))
        min_silence_ms = int(os.getenv("TRANSCRIBE_VAD_MIN_SILENCE_MS", "800"))
        speech_pad_ms = int(os.getenv("TRANSCRIBE_VAD_PAD_MS", "200"))

        speech_segments = get_speech_timestamps(
            wav, model,
            sampling_rate=16000,
            threshold=threshold,
            min_speech_duration_ms=min_speech_ms,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
            return_seconds=True,
        )
    except Exception:
        logger.exception("VAD: get_speech_timestamps a échoué — fallback")
        return None

    if not speech_segments:
        # Audio = tout silence (ou VAD trop strict). Fallback original — Whisper s'en sortira (ou pas).
        logger.warning("VAD: aucun segment parlé détecté (%.1fs total) — fallback original", total_input_sec)
        return None

    max_segments = int(os.getenv("TRANSCRIBE_VAD_MAX_SEGMENTS", "800"))
    if len(speech_segments) > max_segments:
        # Très fragmenté → expression ``aselect`` ffmpeg deviendrait > 32 KB et planterait.
        # Fallback safe sur l'audio non-VAD.
        logger.warning(
            "VAD: %d segments > seuil %d — filter ffmpeg trop long, fallback original",
            len(speech_segments), max_segments,
        )
        return None

    total_speech = sum(float(s["end"]) - float(s["start"]) for s in speech_segments)
    ratio_kept = total_speech / total_input_sec if total_input_sec > 0 else 1.0

    # Sécurité : si VAD garde < 5 % du contenu, c'est presque sûrement un faux négatif (audio
    # entièrement traité comme du bruit). Préférable de fallback que d'envoyer 3 secondes à Whisper.
    if ratio_kept < 0.05:
        logger.warning(
            "VAD: ratio gardé %.1f%% < 5%% — probable faux négatif, fallback original",
            ratio_kept * 100,
        )
        return None

    # Construit l'expression aselect : OR de plages temporelles à garder.
    select_expr = "+".join(
        f"between(t,{float(s['start']):.3f},{float(s['end']):.3f})"
        for s in speech_segments
    )
    # asetpts=N/SR/TB : ré-aligne les timestamps en continu (sinon ffmpeg garde les trous).
    af = f"aselect='{select_expr}',asetpts=N/SR/TB"

    ffmpeg_exe = shutil.which(os.getenv("FFMPEG_BINARY", "ffmpeg"))
    if not ffmpeg_exe:
        logger.warning("VAD: ffmpeg introuvable — skip compression")
        return None

    cmd = [
        ffmpeg_exe,
        "-hide_banner", "-nostdin", "-y", "-loglevel", "warning",
        "-i", str(input_path),
        "-af", af,
        "-map_metadata", "-1",
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "libmp3lame",
        "-b:a", "48k",  # déjà nettoyé, bitrate bas suffit
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=600, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.warning("VAD ffmpeg compression échouée: %s", (e.stderr or "")[:400])
        return None
    except Exception:
        logger.exception("VAD ffmpeg compression exception inattendue")
        return None

    try:
        out_size = output_path.stat().st_size
    except OSError:
        out_size = 0

    if out_size < 1024:
        logger.warning("VAD: output trop petit (%d bytes) — fallback original", out_size)
        try:
            output_path.unlink()
        except OSError:
            pass
        return None

    logger.info(
        "VAD compression OK: %.1fs → %.1fs (%.0f%% gardé), %d segments, %d KB",
        total_input_sec, total_speech, ratio_kept * 100,
        len(speech_segments), out_size // 1024,
    )
    return output_path
