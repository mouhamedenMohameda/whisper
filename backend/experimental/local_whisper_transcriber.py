"""Transcription via le package « openai-whisper » local (sans API cloud).

SUPPRESSION : retirez ce dossier `experimental/` et les références dans routes/transcribe*.py / main / etc.
pour revenir uniquement au mode OpenAI.
"""

from __future__ import annotations

import logging
import os
import threading
from types import SimpleNamespace
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _optional_float_kw(kwargs: dict[str, Any], env_name: str, key: str) -> None:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return
    try:
        kwargs[key] = float(raw)
    except ValueError:
        logger.warning("LOCAL_WHISPER: %s invalide (%r), ignoré.", env_name, raw[:40])


_lock = threading.Lock()
_model_cached: Any = None
_model_key: Optional[str] = None


def is_local_whisper_feature_enabled() -> bool:
    return os.getenv("TRANSCRIBE_LOCAL_WHISPER_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _model_cache_key() -> str:
    name = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_MODEL") or "small").strip() or "small"
    device = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_DEVICE") or "").strip()
    dl = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_DOWNLOAD_ROOT") or "").strip()
    return f"{name}|{device}|{dl}"


def _load_model_locked():
    global _model_cached, _model_key
    key = _model_cache_key()
    if _model_cached is not None and _model_key == key:
        return _model_cached

    try:
        import torch
        import whisper
    except ImportError as e:
        raise RuntimeError(
            "Le module « openai-whisper » ou « torch » est absent — installe-les sur le serveur pour le mode local."
        ) from e

    model_name = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_MODEL") or "small").strip() or "small"
    device_override = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_DEVICE") or "").strip()

    device = device_override if device_override else ("cuda" if torch.cuda.is_available() else "cpu")

    dl_root = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_DOWNLOAD_ROOT") or "").strip() or None

    logger.info(
        "LOCAL_WHISPER: chargement du modèle %s (device=%s, download_root=%s)",
        model_name,
        device,
        dl_root or "(défaut)",
    )

    if dl_root:
        loaded = whisper.load_model(model_name, device=device, download_root=dl_root)
    else:
        loaded = whisper.load_model(model_name, device=device)
    _model_cached = loaded
    _model_key = key
    return _model_cached


def get_loaded_model():
    """Singleton thread-safe."""
    global _model_cached
    if _model_cached is not None and _model_key == _model_cache_key():
        return _model_cached
    with _lock:
        return _load_model_locked()


def transcribe_verbose_one_chunk(audio_path: str, speech_language: str, initial_prompt: Optional[str]) -> Any:
    """
    Retourne un objet façon verbose OpenAI (`text`, `segments`, `language`) pour `_merge_whisper_verbose_responses`.
    """
    import numpy as np
    import torch
    import whisper

    audio = whisper.load_audio(audio_path)
    if audio is None or audio.size == 0:
        raise RuntimeError(
            "Audio vide ou impossible à décoder localement — réessaie en MP3/M4A ou un fichier plus long "
            "(les très courts fichiers multimédias posent parfois problème)."
        )
    audio = np.asarray(audio, dtype=np.float32)
    # Ne jamais utiliser `pad_or_trim` sur tout le fichier : ça TRONQUE à 30 s (N_SAMPLES).
    # `model.transcribe` fait déjà le découpage en fenêtres de 30 s pour les fichiers longs.
    sr = whisper.audio.SAMPLE_RATE
    try:
        min_sec = float(os.getenv("TRANSCRIBE_LOCAL_WHISPER_MIN_PAD_SEC", "1.0"))
    except ValueError:
        min_sec = 1.0
    min_samples = max(int(sr * min_sec), 3200)
    if audio.shape[0] < min_samples:
        audio = np.pad(audio, (0, min_samples - int(audio.shape[0])), mode="constant")
    kwargs: dict[str, Any] = {
        "language": speech_language,
        "verbose": False,
        "fp16": torch.cuda.is_available(),
    }

    temp_raw = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_TEMPERATURE") or "").strip()
    if temp_raw:
        try:
            kwargs["temperature"] = float(temp_raw)
        except ValueError:
            logger.warning("LOCAL_WHISPER: TRANSCRIBE_LOCAL_WHISPER_TEMPERATURE invalide (%r), ignoré.", temp_raw[:40])

    fp16_env = os.getenv("TRANSCRIBE_LOCAL_WHISPER_FP16", "").strip().lower()
    if fp16_env in ("0", "false", "no", "off"):
        kwargs["fp16"] = False
    elif fp16_env in ("1", "true", "yes", "on"):
        kwargs["fp16"] = True

    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt[:2300]

    _optional_float_kw(kwargs, "TRANSCRIBE_LOCAL_WHISPER_COMPRESSION_RATIO_THRESHOLD", "compression_ratio_threshold")
    _optional_float_kw(kwargs, "TRANSCRIBE_LOCAL_WHISPER_LOGPROB_THRESHOLD", "logprob_threshold")
    _optional_float_kw(kwargs, "TRANSCRIBE_LOCAL_WHISPER_NO_SPEECH_THRESHOLD", "no_speech_threshold")

    # Boucles « la la la » : avec `condition_on_previous_text=True`, Whisper peut faire dériver
    # une hallucination sur toutes les fenêtres suivantes. Défaut = false (voir doc Whisper).
    chain_explicit = (os.getenv("TRANSCRIBE_LOCAL_WHISPER_CONDITION_ON_PREVIOUS_TEXT") or "").strip().lower()
    kwargs["condition_on_previous_text"] = chain_explicit in ("1", "true", "yes", "on")

    model = get_loaded_model()
    try:
        result = model.transcribe(audio, **kwargs)
    except (RuntimeError, ValueError) as e:
        low = str(e).lower()
        if "reshape" in low or "0 elements" in low:
            logger.warning("LOCAL_WHISPER: reshape / tenseur vide — %s", e, exc_info=True)
            raise RuntimeError(
                "Segment audio trop court ou illisible pour Whisper local après découpage "
                "(souvent : exporte au format MP3/M4A audio pur et au moins ~2–3 s de parole)."
            ) from e
        raise
    segments_in = result.get("segments") or []

    cleaned: list[dict[str, Any]] = []
    for s in segments_in:
        if not isinstance(s, dict):
            continue
        seg = dict(s)
        txt = str(seg.get("text", "") or "")
        seg["text"] = txt
        seg["start"] = float(seg.get("start", 0) or 0)
        seg["end"] = float(seg.get("end", 0) or 0)
        cleaned.append(seg)

    txt_out = str(result.get("text", "") or "").strip()
    lang = result.get("language") or speech_language
    return SimpleNamespace(text=txt_out, segments=cleaned, language=str(lang or speech_language))


def whisper_progress_rt_factor_local() -> float:
    raw = os.getenv("WHISPER_PROGRESS_RT_FACTOR_LOCAL", "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return 10.0
