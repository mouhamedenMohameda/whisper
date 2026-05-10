from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
from typing import Annotated, Any, AsyncIterator, Optional

import openai
from tinytag import TinyTag
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from credits_wallet import debit_credits
from database import get_db
from deps import require_wallet_user
from models import User
from pricing import (
    billed_mru_to_wallet_units_debit,
    estimate_tokens_from_chars,
    openai_transcribe_chat_provider_usd,
    transcribe_aggregate_billed_mru,
    wallet_units_to_mru_display,
    whisper_provider_usd,
)

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_EXTENSIONS = frozenset({
    ".mp3",
    ".wav",
    ".m4a",
    ".mp4",
    ".m4b",
    ".ogg",
    ".oga",
    ".opus",
    ".flac",
    ".aac",
    ".caf",
    ".aiff",
    ".aif",
    ".wma",
    ".amr",
})

# Upload HTTP : fichiers volumineux acceptés ; découpage automatique pour chaque appel Whisper (voir OPENAI_WHISPER_MAX_UPLOAD_MB).
MAX_SIZE_MB = int(os.getenv("TRANSCRIBE_MAX_MB", "2048"))
MAX_DURATION_SECONDS = int(os.getenv("TRANSCRIBE_MAX_DURATION_SECONDS", str(2 * 3600)))
# Taille maximale d’un seul fichier envoyé à l’API « whisper-1 » (OpenAI) — le serveur découpe au‑delà.
OPENAI_WHISPER_MAX_UPLOAD_MB = float(os.getenv("OPENAI_WHISPER_MAX_UPLOAD_MB", "25"))


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _select_preprocess_mp3_bitrate(duration_seconds: Optional[float]) -> str:
    """Bitrate cible sous la limite OpenAI sur enregistrements longs (MP3 mono 16 kHz)."""
    configured = os.getenv("TRANSCRIBE_MP3_BITRATE", "").strip()
    if configured:
        return configured
    if duration_seconds is None:
        return "40k"
    if duration_seconds > 45 * 60:
        return "20k"
    if duration_seconds > 20 * 60:
        return "24k"
    if duration_seconds > 8 * 60:
        return "32k"
    return "40k"


def _ffmpeg_preprocess_to_mp3(src_path: str, duration_estimate: Optional[float]) -> Optional[str]:
    """
    Normalise pour Whisper : mono 16 kHz, coupe basses parasite, nivellement dynamique léger.

    Réduit aussi le risque d’être « perturbé » par niveau très bas sans être une VAD RNNoise dédiée.
    Retourne un chemin .mp3 temporaire à supprimer après usage, ou None si repli sur l’original.
    """
    if not _env_truthy("TRANSCRIBE_ENABLE_AUDIO_PREPROCESS", True):
        return None
    ffmpeg_exe = shutil.which(os.getenv("FFMPEG_BINARY", "ffmpeg"))
    if not ffmpeg_exe:
        logger.info("TRANSCRIBE: ffmpeg introuvable (PATH). Pré-traitement désactivé.")
        return None

    filt = os.getenv(
        "TRANSCRIBE_AUDIO_FILTER",
        "highpass=f=90,dynaudnorm=f=210:g=27",
    )
    br = _select_preprocess_mp3_bitrate(duration_estimate)

    fd, dst = tempfile.mkstemp(suffix=".lecturai.pre.mp3")
    os.close(fd)

    timeout_s = float(os.getenv("TRANSCRIBE_FFMPEG_TIMEOUT_SEC", "840"))
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "warning",
        "-i",
        src_path,
        "-map_metadata",
        "-1",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        filt,
        "-c:a",
        "libmp3lame",
        "-b:a",
        br,
        dst,
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            timeout=max(30.0, timeout_s),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        logger.warning("TRANSCRIBE: exécutable ffmpeg introuvable (%s)", ffmpeg_exe)
        try:
            os.unlink(dst)
        except OSError:
            pass
        return None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()
        logger.warning(
            "TRANSCRIBE: ffmpeg échec (code %s). stderr (extrait): %s",
            e.returncode,
            err[:520],
        )
        try:
            os.unlink(dst)
        except OSError:
            pass
        return None
    except subprocess.TimeoutExpired:
        logger.warning("TRANSCRIBE: ffmpeg dépassé après %.0fs — repli sur l’original.", timeout_s)
        try:
            os.unlink(dst)
        except OSError:
            pass
        return None

    try:
        out_sz = os.path.getsize(dst)
    except OSError:
        try:
            os.unlink(dst)
        except OSError:
            pass
        return None

    max_allowed = int(OPENAI_WHISPER_MAX_UPLOAD_MB * 1024 * 1024 * 0.97)
    if out_sz > max_allowed:
        logger.warning(
            "TRANSCRIBE: sortie ffmpeg %s octets > enveloppe Whisper (~%s) — envoi fichier original.",
            out_sz,
            max_allowed,
        )
        try:
            os.unlink(dst)
        except OSError:
            pass
        return None

    logger.info(
        "TRANSCRIBE: ffmpeg OK — %s octets, filter=%r, bitrate=%s, duration_est=%s",
        out_sz,
        filt[:160],
        br,
        duration_estimate,
    )
    return dst


def _whisper_upload_byte_budget() -> int:
    """Marge sous la limite fichier OpenAI whisper-1."""
    return int(OPENAI_WHISPER_MAX_UPLOAD_MB * 1024 * 1024 * 0.88)


def _mp3_bitrate_bps(bitrate_s: str) -> int:
    s = (bitrate_s or "40k").strip().lower()
    m = re.match(r"^(\d+)\s*k$", s)
    if m:
        return int(m.group(1)) * 1000
    digits = re.sub(r"\D", "", s)
    return int(digits or "40") * 1000


def _ffprobe_duration_seconds(path: str) -> Optional[float]:
    ffprobe = shutil.which(os.getenv("FFPROBE_BINARY", "ffprobe"))
    if not ffprobe:
        return None
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=min(180.0, float(os.getenv("TRANSCRIBE_FFPROBE_TIMEOUT_SEC", "120"))),
            check=False,
        )
        if out.returncode != 0:
            return None
        v = float((out.stdout or "").strip())
        return v if v > 0 else None
    except Exception:
        return None


def _ffmpeg_extract_mono_mp3_chunk(
    src_path: str,
    start_sec: float,
    duration_sec: float,
    bitrate_str: str,
    audio_filter: str,
) -> str:
    ffmpeg_exe = shutil.which(os.getenv("FFMPEG_BINARY", "ffmpeg"))
    if not ffmpeg_exe:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg introuvable sur le serveur : requis pour découper les fichiers dépassant la limite Whisper.",
        )
    fd, dst = tempfile.mkstemp(suffix=".lecturai.chunk.mp3")
    os.close(fd)
    timeout_s = float(os.getenv("TRANSCRIBE_FFMPEG_TIMEOUT_SEC", "840"))
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "warning",
        "-i",
        src_path,
        "-ss",
        f"{max(0.0, start_sec):.3f}",
        "-t",
        f"{max(0.1, duration_sec):.3f}",
        "-map_metadata",
        "-1",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        audio_filter,
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate_str,
        dst,
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            timeout=max(60.0, timeout_s),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="ffmpeg introuvable pour le découpage audio.") from None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()
        logger.warning("TRANSCRIBE: ffmpeg segment échec: %s", err[:520])
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise HTTPException(
            status_code=400,
            detail="Échec du découpage audio sur le serveur (ffmpeg). Réessaie ou réencode le fichier.",
        ) from None
    except subprocess.TimeoutExpired:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise HTTPException(status_code=504, detail="Découpage audio trop long (timeout ffmpeg).") from None

    try:
        sz = os.path.getsize(dst)
    except OSError:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="Segment audio illisible après découpage.") from None

    if sz > _whisper_upload_byte_budget() * 1.08:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise HTTPException(
            status_code=400,
            detail=(
                "Un segment reste trop volumineux pour l’API Whisper après découpage. "
                "Baisse TRANSCRIBE_MP3_BITRATE ou augmente OPENAI_WHISPER_MAX_UPLOAD_MB côté serveur."
            ),
        )
    if sz < 256:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Segment audio vide ou trop court après découpage.") from None

    return dst


def _prepare_whisper_chunks(
    tmp_path: str,
    estimated_duration: Optional[float],
) -> tuple[list[tuple[str, float]], list[str], bool]:
    """
    Retourne (liste (chemin_fichier, offset_temps_sec), chemins_segments_à_supprimer, preprocess_mono_appliqué).

    preprocess_mono_appliqué = True seulement si un seul MP3 normalisé couvre tout le média.
    """
    budget = _whisper_upload_byte_budget()
    processed = _ffmpeg_preprocess_to_mp3(tmp_path, estimated_duration)

    if processed:
        try:
            ps = os.path.getsize(processed)
        except OSError:
            ps = 0
        if 0 < ps <= budget:
            return [(processed, 0.0)], [], True

    try:
        orig_sz = os.path.getsize(tmp_path)
    except OSError:
        orig_sz = 0

    if orig_sz > 0 and orig_sz <= budget:
        return [(tmp_path, 0.0)], [], False

    total_dur = estimated_duration if estimated_duration and estimated_duration > 0 else None
    if total_dur is None:
        total_dur = _ffprobe_duration_seconds(tmp_path)

    if total_dur is None or total_dur <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Impossible de lire la durée du média pour le découpage automatique. "
                "Vérifie que ffmpeg/ffprobe est installé sur le serveur, ou réencode en MP3/M4A avec métadonnées."
            ),
        )

    _reject_if_duration_exceeds(total_dur)

    br_str = _select_preprocess_mp3_bitrate(total_dur)
    filt = os.getenv(
        "TRANSCRIBE_AUDIO_FILTER",
        "highpass=f=90,dynaudnorm=f=210:g=27",
    )
    bytes_per_sec = max(2000, _mp3_bitrate_bps(br_str) // 8)
    max_chunk_sec = float(os.getenv("TRANSCRIBE_MAX_CHUNK_SECONDS", "7200"))
    chunk_dur = max(60.0, min(max_chunk_sec, (budget * 0.82) / bytes_per_sec))

    seg_paths: list[str] = []
    chunks: list[tuple[str, float]] = []
    n = int(math.ceil(total_dur / chunk_dur))
    for i in range(n):
        start = i * chunk_dur
        dur = min(chunk_dur, total_dur - start)
        if dur < 0.5:
            break
        part = _ffmpeg_extract_mono_mp3_chunk(tmp_path, start, dur, br_str, filt)
        chunks.append((part, start))
        seg_paths.append(part)

    logger.info(
        "TRANSCRIBE: découpage automatique — %s segment(s) Whisper (~%.0f s / segment, durée totale %.0f s).",
        len(chunks),
        chunk_dur,
        total_dur,
    )
    return chunks, seg_paths, False


def _segment_dict_with_offset(seg: Any, time_offset: float) -> dict[str, Any]:
    d: dict[str, Any]
    if isinstance(seg, dict):
        d = dict(seg)
    elif hasattr(seg, "model_dump"):
        try:
            d = dict(seg.model_dump(mode="python"))  # type: ignore[arg-type]
        except Exception:
            d = {}
    else:
        d = {}
        for attr in (
            "id",
            "seek",
            "start",
            "end",
            "text",
            "tokens",
            "temperature",
            "avg_logprob",
            "compression_ratio",
            "no_speech_prob",
        ):
            if hasattr(seg, attr):
                try:
                    d[attr] = getattr(seg, attr)
                except Exception:
                    pass
    txt = str(d.get("text", "") if "text" in d else getattr(seg, "text", "") or "")
    d["text"] = txt
    d["start"] = float(d.get("start", 0) or 0) + time_offset
    d["end"] = float(d.get("end", 0) or 0) + time_offset
    return d


def _merge_whisper_verbose_responses(responses: list[Any], time_offsets: list[float]) -> Any:
    """Fusionne plusieurs réponses verbose_json (segments décalés, texte concaténé)."""
    if not responses:
        return SimpleNamespace(text="", segments=[], language="unknown")
    if len(responses) == 1:
        return responses[0]

    all_segs: list[dict[str, Any]] = []
    texts: list[str] = []
    lang = getattr(responses[0], "language", None) or "unknown"

    for resp, off in zip(responses, time_offsets):
        t = (getattr(resp, "text", None) or "").strip()
        if t:
            texts.append(t)
        for seg in getattr(resp, "segments", None) or []:
            all_segs.append(_segment_dict_with_offset(seg, off))

    merged_text = " ".join(texts).strip()
    return SimpleNamespace(text=merged_text, segments=all_segs, language=lang)


def _seg_start(seg: Any) -> float:
    if isinstance(seg, dict):
        return float(seg.get("start", 0) or 0)
    return float(seg.start)


def _seg_text(seg: Any) -> str:
    if isinstance(seg, dict):
        return seg.get("text") or ""
    return seg.text or ""


def _seg_metric_float(seg: Any, key: str) -> Optional[float]:
    """Lit un champ numérique Whisper sur un segment (dict pydantic-like ou objet)."""
    try:
        if isinstance(seg, dict):
            if key not in seg:
                return None
            v = seg.get(key)
        else:
            v = getattr(seg, key, None)
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _ordered_text_segments_for_timestamp_structure(segments: list) -> list[Any]:
    """
    Même ordre et filtrage que `_timestamp_structure` : tri par `start`, texte non vide uniquement.
    L’index dans cette liste est le `segment_index_whisper` / `sid` des blocs mixed view.
    """
    ordered = sorted(segments or [], key=_seg_start)
    out: list[Any] = []
    for seg in ordered:
        raw = _seg_text(seg)
        if not raw:
            continue
        out.append(seg)
    return out


def _whisper_reliability_payload(seg: Any) -> dict[str, Any]:
    """
    Agrège les scores segmentaires renvoyés par Whisper (verbose_json) et produit un indicateur
    « haute fiabilité ASR » pour surlignage côté client. Les seuils sont surtout calibrés pour le français
    et l’arabe cours ; ajustables via variables d’environnement.
    """
    nsp = _seg_metric_float(seg, "no_speech_prob")
    alp = _seg_metric_float(seg, "avg_logprob")
    cr = _seg_metric_float(seg, "compression_ratio")
    temp = _seg_metric_float(seg, "temperature")

    try:
        nsp_max = float(os.getenv("TRANSCRIBE_RELIABLE_NO_SPEECH_PROB_MAX", "0.45"))
    except ValueError:
        nsp_max = 0.45
    try:
        alp_min = float(os.getenv("TRANSCRIBE_RELIABLE_AVG_LOGPROB_MIN", "-0.55"))
    except ValueError:
        alp_min = -0.55
    try:
        cr_max = float(os.getenv("TRANSCRIBE_RELIABLE_COMPRESSION_RATIO_MAX", "2.55"))
    except ValueError:
        cr_max = 2.55

    # Score indicatif 0–100 (moyenne de composantes normalisées), pour infobulle / tri manuel.
    score_parts: list[float] = []
    if nsp is not None and nsp_max > 0:
        score_parts.append(max(0.0, min(100.0, (nsp_max - nsp) / nsp_max * 100.0)))
    if alp is not None:
        lo, hi = -1.05, 0.0
        if hi > lo:
            score_parts.append(max(0.0, min(100.0, (alp - lo) / (hi - lo) * 100.0)))
    if cr is not None and cr_max > 1.0:
        score_parts.append(max(0.0, min(100.0, (cr_max - cr) / (cr_max - 1.0) * 100.0)))

    score_0_100 = round(sum(score_parts) / len(score_parts), 1) if score_parts else 0.0

    high = True
    if nsp is not None and nsp > nsp_max:
        high = False
    if alp is not None and alp < alp_min:
        high = False
    if cr is not None and cr > cr_max:
        high = False
    if nsp is None and alp is None and cr is None:
        high = False

    return {
        "high_reliability": bool(high),
        "score_0_100": score_0_100,
        "avg_logprob": alp,
        "no_speech_prob": nsp,
        "compression_ratio": cr,
        "temperature": temp,
        "thresholds": {
            "no_speech_prob_max": nsp_max,
            "avg_logprob_min": alp_min,
            "compression_ratio_max": cr_max,
        },
    }


def build_verbatim_transcript(segments: list, fallback_text: str) -> str:
    if not segments:
        return (fallback_text or "").strip()
    return "".join(_seg_text(s) for s in segments).strip()


def build_timestamped_transcript(segments: list, fallback_text: str) -> str:
    """Insert [MM:SS] markers at each 30-second boundary from t=0 up through the segment times."""
    if not segments:
        return (fallback_text or "").strip()

    ordered = sorted(segments, key=_seg_start)
    parts: list[str] = []
    last_placed_block = -1

    for seg in ordered:
        text = _seg_text(seg)
        if not text:
            continue
        start = _seg_start(seg)
        block_idx = int(start // 30)

        while last_placed_block < block_idx:
            last_placed_block += 1
            marker_sec = last_placed_block * 30
            mm, ss = divmod(marker_sec, 60)
            parts.append(f"[{mm:02d}:{ss:02d}]")

        parts.append(text)

    return "".join(parts).strip()


def _timestamp_structure(
    segments: list,
    fallback_text: str,
) -> list[tuple[str, Optional[int], str]]:
    """
    Même succession que la transcription avec repères temporels : soit un marqueur,
    soit un bloc texte (index du segment textuel pour corrélation).
    """
    if not segments:
        t = (fallback_text or "").strip()
        return [("text", 0, t)] if t else []
    ordered = sorted(segments, key=_seg_start)
    out: list[tuple[str, Optional[int], str]] = []
    last_placed_block = -1
    seg_ix = -1

    for seg in ordered:
        raw = _seg_text(seg)
        if not raw:
            continue
        seg_ix += 1
        start = _seg_start(seg)
        block_idx = int(start // 30)

        while last_placed_block < block_idx:
            last_placed_block += 1
            marker_sec = last_placed_block * 30
            mm, ss = divmod(marker_sec, 60)
            out.append(("marker", None, f"[{mm:02d}:{ss:02d}]"))

        out.append(("text", seg_ix, raw))

    return out


def _mixed_lang_primary_codes(speech_language: str) -> frozenset[str]:
    if speech_language == "ar":
        return frozenset({"ar"})
    return frozenset({"fr"})


def _mixed_lang_target_label(speech_language: str) -> str:
    if speech_language == "ar":
        return "Modern Standard Arabic (formal Arabic suitable for coursework)"
    return "French"


def _text_is_mostly_digits_or_symbols(s: str) -> bool:
    letters = sum(1 for c in s if c.isalpha())
    return letters < 4


try:
    from langdetect import LangDetectException, detect_langs  # noqa: PLC0415
except ImportError:  # pragma: no cover — optional degrade
    detect_langs = None  # type: ignore[misc, assignment]

    class LangDetectException(Exception):  # type: ignore[misc]
        pass


# Scripts « parasites » hors alphabétique latine française (hallucinations / mélanges ASR malgré langue=fr).
_RE_SCRIPT_PARASITE_FR = re.compile(
    r"[\u3040-\u30ff\u4e00-\u9fff\u3000-\u303f\uac00-\ud7af"
    r"\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uff66-\uffdc"
    r"\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\uFB50-\uFDFF\uFE70-\uFEFF"
    r"\u0400-\u04ff\u0500-\u052f\u2DE0-\u2DFF\uA640-\uA69F"
    r"\u0590-\u05ff]",
    re.UNICODE,
)

# Dans un cours en arabe, tout bloc latin long est généralement de l’anglais/français parasite ; le latin court (sigles) passe.
_RE_SCRIPT_PARASITE_AR_EXTRA = re.compile(
    r"[\u3040-\u30ff\u4e00-\u9fff\u3000-\u303f\uac00-\ud7af"
    r"\u0400-\u04ff\u0500-\u052f]",
    re.UNICODE,
)

_RE_LATIN_SNIPPETS = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿ](?:[\w'\-À-ÖØ-öø-ÿ]*\s+[A-Za-zÀ-ÖØ-öø-ÿ][\w'\-À-ÖØ-öø-ÿ]*)+",
)

# À retirer de la vue affichée si le modèle en laisse passer (sans toucher au texte latin ni arabe).
_RE_RESIDUAL_ASR_CJK_ONLY = re.compile(
    r"[\u3040-\u30ff\u4e00-\u9fff\u3000-\u303f\uac00-\ud7af"
    r"\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uff66-\uffdc]+",
)


def _scrub_residual_cjk_from_display(txt: Optional[str]) -> str:
    s = txt or ""
    return _RE_RESIDUAL_ASR_CJK_ONLY.sub("", s)


def _detect_language_hint(text_norm: str) -> tuple[Optional[str], float]:
    """Estimateur léger Langdetect (court texte toléré pour les snippets anglais inclus)."""
    if detect_langs is None:
        return None, 0.0
    compact = "".join(text_norm.split())
    if len(compact) < 8:
        return None, 0.0
    try:
        ranked = detect_langs(text_norm)
    except LangDetectException:
        return None, 0.0
    if not ranked:
        return None, 0.0
    top = ranked[0]
    return top.lang, float(top.prob)


def _mixed_lang_confidence_floor() -> float:
    raw = os.getenv("TRANSCRIBE_MIXED_LANG_CONFIDENCE")
    try:
        v = float(raw) if raw and raw.strip() else 0.88
        return max(0.55, min(0.99, v))
    except ValueError:
        return 0.88


def _mixed_lang_confidence_short() -> float:
    raw = os.getenv("TRANSCRIBE_MIXED_LANG_CONFIDENCE_SHORT")
    try:
        v = float(raw) if raw and raw.strip() else 0.78
        return max(0.55, min(0.99, v))
    except ValueError:
        return 0.78


def _confidence_for_clause(clause: str, base: float) -> float:
    L = len(clause.strip())
    if L <= 24:
        return min(base, max(0.65, base - 0.12))
    if L <= 56:
        return min(base, max(0.68, base - 0.07))
    if L <= 110:
        return min(base, max(0.72, base - 0.03))
    return base


def _partition_script_parasites(text: str, speech_language: str) -> list[tuple[str, bool]]:
    """Découpe en morceaux (texte normal vs script manifestement parasite pour la langue du cours)."""
    if speech_language == "fr":
        pat = _RE_SCRIPT_PARASITE_FR
    elif speech_language == "ar":
        pat = _RE_SCRIPT_PARASITE_AR_EXTRA
    else:
        pat = None
    if pat is None or not text:
        return [(text, False)]
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in pat.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return [(a, b) for a, b in out if a]


def _french_sentence_markers(t: str) -> int:
    s = re.sub(r"[\u2019\u2018']", "'", (t or "").lower())
    return len(
        re.findall(
            r"\b(?:nous|vous|ils|elles|on|sommes|sont|été|être|avec|pour|dans|cette|c'est|c’est|cet|ces|dont|leur|qui|"
            r"que|qu'|il|elle|un|une|des|du|de|la|le|les|aux|son|sa|ses|après|avant|comme|très|aussi)\b",
            s,
        ),
    )


def _english_signal_fr(t: str) -> int:
    return len(
        re.findall(
            r"\b(?:the|and|or|of|for|with|from|into|that|this|these|those|there|then|than|when|where|while|"
            r"which|would|could|should|must|have|has|had|been|being|according|because|during|among|amongst|"
            r"overall|toward|towards|through|against|structure|progress|according|example|however|therefore)\b",
            t,
            flags=re.IGNORECASE,
        ),
    )


def _split_clauses(text: str) -> list[str]:
    t = text.replace("\r", "")
    if not t.strip():
        return []
    parts = re.split(r"(?<=[!?])\s+|(?<=\.)\s+|\s*\n\s*", t)
    return [p for p in parts if p]


def _collapse_adjacent_like_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fusionner les voisins identiques pour limiter les sur-requêtes (espaces Whisper conservés)."""
    out: list[dict[str, Any]] = []
    for sp in spans:
        if (
            out
            and out[-1].get("foreign") == sp.get("foreign")
            and (
                str(out[-1].get("detected_lang") or "")
                == str(sp.get("detected_lang") or "")
            )
            and (str(out[-1].get("reason") or "") == str(sp.get("reason") or ""))
        ):
            out[-1]["text"] = str(out[-1].get("text") or "") + str(sp.get("text") or "")
        else:
            out.append(dict(sp))
    return out


def _classify_whole_text_fragment(
    t: str,
    speech_language: str,
    min_chars: int,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Si un bloc court est nettement entièrement hors langue du cours."""
    trimmed = (t or "").strip()
    if len(trimmed) < min_chars:
        return False, None, None
    if _text_is_mostly_digits_or_symbols(trimmed):
        return False, None, None

    primary = _mixed_lang_primary_codes(speech_language)
    dh, pb = _detect_language_hint(trimmed)
    thr = _confidence_for_clause(trimmed, _mixed_lang_confidence_floor())
    short_thr = max(0.70, min(thr - 0.08, _mixed_lang_confidence_short()))

    fr_m = _french_sentence_markers(trimmed)
    en_s = _english_signal_fr(trimmed)

    if speech_language == "fr":
        # Phrase française longue peu polluée d’anglais — décomposer plutôt qu’engloutir tout le segment.
        if fr_m >= 5 and pb < 0.93:
            pass
        else:
            if dh and dh not in primary and pb >= thr:
                return True, dh, "langdetect"
            if dh and dh not in primary and len(trimmed) <= 76 and pb >= short_thr:
                return True, dh, "langdetect_short"
        # Anglais lexical manifeste sur segment court-ish
        if en_s >= 4 and dh == "fr" and len(trimmed) < 220:
            return True, "en", "lexical_en_mix"
        if en_s >= 5 and dh in primary and pb < 0.72:
            return True, "en", "lexical_en_dom"
        return False, dh, None

    # cours en arabe
    if dh and dh not in primary and pb >= thr:
        return True, dh, "langdetect"
    if dh and dh not in primary and len(trimmed) <= 92 and pb >= short_thr:
        return True, dh, "langdetect_short"
    return False, dh, None


def _latin_tail_foreign_fr(latin: str, clause: str, min_clause_chars: int) -> tuple[bool, Optional[str], Optional[str]]:
    raw = latin.strip()
    if len(raw) < 8:
        return False, None, None
    if _text_is_mostly_digits_or_symbols(raw):
        return False, None, None

    fr_ctx = _french_sentence_markers(clause)
    en_lex = _english_signal_fr(raw)
    dh, pb = _detect_language_hint(raw)
    thr = _confidence_for_clause(raw, min(_mixed_lang_confidence_floor(), _mixed_lang_confidence_short()))

    prim = frozenset({"fr"})
    if dh == "en" and pb >= max(0.72, thr - 0.10):
        if fr_ctx >= 6 and pb < 0.90 and en_lex <= 4:
            return False, dh, None
        return True, "en", "langdetect_tail"

    if dh and dh not in prim | {"en"} and pb >= thr:
        return True, dh, "langdetect_tail"

    if len(raw) >= 12:
        fr_in = _french_sentence_markers(raw)
        if (
            dh == "en"
            or (en_lex >= 2 and fr_in <= 2 and re.search(r"\b(of|the|and|according|during|overall|among)\b", raw, re.I))
        ):
            if fr_ctx >= 7 and en_lex <= 5 and dh == "fr" and pb >= 0.88:
                return False, dh, None
            if en_lex >= 2:
                return True, "en", "lexical_tail"

    return False, dh, None


def _clause_to_fr_spans(clause: str, min_clause_chars: int) -> list[dict[str, Any]]:
    wf, wl, wr = _classify_whole_text_fragment(clause, "fr", min_clause_chars)
    if wf:
        return [{"text": clause, "foreign": True, "detected_lang": wl or "mis", "reason": wr or "foreign"}]

    spans: list[dict[str, Any]] = []
    idx = 0
    lat_i = tuple(_RE_LATIN_SNIPPETS.finditer(clause))
    if not lat_i:
        wf2, wl2, wr2 = _classify_whole_text_fragment(clause, "fr", min(10, max(8, min_clause_chars - 4)))
        if wf2:
            return [{"text": clause, "foreign": True, "detected_lang": wl2 or "mis", "reason": wr2}]
        return [{"text": clause, "foreign": False, "detected_lang": None, "reason": None}]
    for m in lat_i:
        if m.start() > idx:
            head = clause[idx:m.start()]
            if head:
                spans.append(
                    {"text": head, "foreign": False, "detected_lang": None, "reason": None},
                )
        piece = m.group(0)
        fo, dl, rr = _latin_tail_foreign_fr(piece, clause, min_clause_chars)
        spans.append(
            {"text": piece, "foreign": fo, "detected_lang": (dl if fo else None), "reason": (rr if fo else None)},
        )
        idx = m.end()
    if idx < len(clause):
        tail = clause[idx:]
        if tail:
            spans.append({"text": tail, "foreign": False, "detected_lang": None, "reason": None})

    spans = _collapse_adjacent_like_spans(spans)
    return spans if spans else [{"text": clause, "foreign": False, "detected_lang": None, "reason": None}]


def _latin_foreign_for_ar_clause(latin: str, clause: str, min_clause_chars: int) -> tuple[bool, Optional[str], Optional[str]]:
    raw = latin.strip()
    if len(raw) < 10:
        return False, None, None
    if _text_is_mostly_digits_or_symbols(raw):
        return False, None, None

    dh, pb = _detect_language_hint(raw)
    thr = _confidence_for_clause(raw, _mixed_lang_confidence_floor())
    prim = frozenset({"ar"})
    if dh and dh not in prim:
        short_ok = pb >= max(0.68, thr - 0.12)
        full_ok = pb >= thr
        if full_ok or (len(raw) <= 88 and short_ok):
            return True, dh, "latin_tail"
        if dh in frozenset({"en", "fr", "de", "es", "pt", "it"}) and len(raw) >= 18:
            latin_ratio = sum(1 for c in raw if ord(c) < 128 or c.isalpha()) / max(1, len(raw))
            if latin_ratio > 0.78 and pb >= 0.70:
                return True, dh, "latin_probable"
    return False, dh, None


def _clause_to_ar_spans(clause: str, min_clause_chars: int) -> list[dict[str, Any]]:
    wf, wl, wr = _classify_whole_text_fragment(clause, "ar", min_clause_chars)
    if wf:
        return [{"text": clause, "foreign": True, "detected_lang": wl or "mis", "reason": wr or "foreign"}]

    spans = []
    idx = 0
    latin_iter = tuple(_RE_LATIN_SNIPPETS.finditer(clause))
    if not latin_iter:
        return [{"text": clause, "foreign": False, "detected_lang": None, "reason": None}]
    for m in latin_iter:
        if m.start() > idx:
            head = clause[idx:m.start()]
            if head.strip():
                spans.append({"text": head, "foreign": False, "detected_lang": None, "reason": None})
        piece = m.group(0)
        fo, dl, rr = _latin_foreign_for_ar_clause(piece, clause, min_clause_chars)
        spans.append({"text": piece, "foreign": fo, "detected_lang": (dl if fo else None), "reason": (rr if fo else None)})
        idx = m.end()
    if idx < len(clause):
        tail = clause[idx:]
        if tail:
            spans.append({"text": tail, "foreign": False, "detected_lang": None, "reason": None})

    spans = _collapse_adjacent_like_spans(spans)
    return spans if spans else [{"text": clause, "foreign": False, "detected_lang": None, "reason": None}]


def _mixed_spans_whisper_fragment(blob: str, speech_language: str, min_clause_chars: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for frag, parasite in _partition_script_parasites(blob, speech_language):
        if parasite:
            result.append(
                {
                    "text": frag,
                    "foreign": True,
                    "detected_lang": "mis",
                    "reason": "script_parasite",
                },
            )
            continue

        clauses = _split_clauses(frag)
        seq = clauses if clauses else [frag]
        for clause in seq:
            if speech_language == "fr":
                result.extend(_clause_to_fr_spans(clause, min_clause_chars))
            else:
                result.extend(_clause_to_ar_spans(clause, min_clause_chars))

    return _collapse_adjacent_like_spans(result)


def _segments_to_processing_dicts(segments: list[Any]) -> list[dict[str, Any]]:
    """Représentation mutable (start/end/text) tant pour pydantic Whisper que pour dict legacy."""
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(segments or []):
        d: dict[str, Any]
        if isinstance(seg, dict):
            d = dict(seg)
        elif hasattr(seg, "model_dump"):
            try:
                d = dict(seg.model_dump(mode="python"))  # type: ignore[arg-type]
            except Exception:
                d = {}
        else:
            d = {}
            for attr in (
                "id",
                "seek",
                "start",
                "end",
                "text",
                "tokens",
                "temperature",
                "avg_logprob",
                "compression_ratio",
                "no_speech_prob",
            ):
                if hasattr(seg, attr):
                    try:
                        d[attr] = getattr(seg, attr)
                    except Exception:
                        pass

        txt = str(d.get("text", "") if "text" in d else getattr(seg, "text", "") or "")
        d["text"] = txt
        d.setdefault("start", float(d.get("start", 0) or 0))
        d.setdefault("end", float(d.get("end", 0) or 0))
        d["_poly_ix"] = i
        out.append(d)
    return out


def _semantic_polish_one_batch_openai(
    client: openai.OpenAI,
    model: str,
    speech_language: str,
    subject_snip: str,
    approximate_prev_tail: str,
    batch: list[dict[str, Any]],
) -> tuple[dict[int, str], Optional[dict[str, int]], str]:
    """
    Une passe Chat sur un lot de segments. Retour dict index -> nouveau texte, usage tokens et la queue de fusion.
    """
    items = []
    for d in batch:
        ix = int(d["_poly_ix"])
        txt = str(d.get("text") or "")
        items.append(
            {
                "idx": ix,
                "start": round(float(d.get("start") or 0), 4),
                "end": round(float(d.get("end") or 0), 4),
                "text": txt[:6200],
            },
        )

    inp = {"subject": subject_snip[:400], "preceding_text_tail": (approximate_prev_tail or "")[-900:], "segments": items}
    payload_u = json.dumps(inp, ensure_ascii=False)

    if speech_language == "fr":
        sys_m = (
            "Tu prépares une retranscription de cours pour des étudiants. "
            "Tu reçois des segments Whisper en français forcé (langue officielle=déjà française). "
            "Corrige UNIQUEMENT les erreurs de reconnaissance VOCALE manifestes où le français écrit doit être restauré dans un contexte pédagogique cohérent.\n\n"
            "Règles strictes :\n"
            "- Ne change PAS le fond : ne résume pas, n’explique pas, n’« améliore » pas la phrase si elle est plausible.\n"
            "- Respecte jargon / noms propres plausiblement présents dans le cours (thème indiqué par subject).\n"
            "- Gère intelligemment les confusions HOMOPHONES / quasi-homophones typiques DU FRANÇAIS PARLÉ vers l’écrit quand la cohérence grammaticale + pédagogique le demande fortement "
            '(exemple d’illus : « encore » vs liaison « … un corps … » aberrante ponctuelle — corriger SI le passage est clairement aberrant).\n'
            "- N’injecte aucune liste fermée : aucun dictionnaire imposé, raisonnement par contexte seulement.\n"
            "- Préserve l’aspect segmenté : même nombre de segments, mêmes champs idx dans la sortie.\n"
            "- Préserve l’indentation spatiale Whisper quand pertinent (espaces début segment inchangés).\n\n"
            "Sortie JSON UNIQUE : {\"revised\":[{\"idx\":NUMBER,\"text\":STRING}]} — tous les segments d’entrée. "
            "Pour un segment irréparable ou incertain, renvoie le texte IDENTIQUE inchangé."
        )
    else:
        sys_m = (
            "You align oral Arabic lecture transcripts to clear Modern Standard Arabic (فصحى) for coursework. "
            "Input Whisper segments are requested in Arabic script but may blend dialect forms (Darija/Maghreb, Mashreq, Hassaniya tendencies, Gulf, Egyptian/Levant mixes, codeswitching stubs).\n\n"
            "Hard rules :\n"
            "- Normalize dialectal morphology / common spoken particles toward formal MSA that matches educational reading while preserving factual meaning.\n"
            "- Do NOT translate non-Arabic course glosses gratuitously unless they are stray Latin noise wrongly inserted.\n"
            "- Do NOT invent Quranic quotations or technical claims.\n"
            "- Keep proper nouns plausible for the discipline (subject).\n"
            "- Same segment count — same idx set; output {\"revised\":[{\"idx\":NUMBER,\"text\":STRING}]} only.\n"
            "- Preserve leading/trailing Whisper spacing quirks when harmless.\n"
            "- Where uncertain between two plausible MSA synonyms, prefer the academically neutral wording."
        )

    tok_use: Optional[dict[str, int]] = None
    try:
        comp = client.chat.completions.create(
            model=model,
            temperature=0.08,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys_m},
                {"role": "user", "content": payload_u},
            ],
        )
    except Exception:
        logger.warning("TRANSCRIBE: polish batch Chat failed.", exc_info=True)
        return {}, None, approximate_prev_tail

    try:
        u = getattr(comp, "usage", None)
        if u is not None:
            tok_use = {
                "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
            }
    except Exception:
        tok_use = None

    revised: dict[int, str] = {}
    try:
        ch0 = comp.choices[0]
        txt = (ch0.message.content or "").strip()
        data = json.loads(txt)
        for row in data.get("revised") or []:
            si = row.get("idx")
            tt = row.get("text") or row.get("t")
            if si is None or tt is None:
                continue
            revised[int(si)] = str(tt)
    except Exception:
        logger.warning("TRANSCRIBE: polish JSON invalide ou vide; batch ignoré.")

    tail = approximate_prev_tail or ""
    ordered = sorted(batch, key=lambda z: int(z["_poly_ix"]))
    for d in ordered:
        ix_i = int(d["_poly_ix"])
        tail += revised.get(ix_i, str(d.get("text") or ""))
    fusion_tail = tail[-5200:] if tail else ""
    return revised, tok_use, fusion_tail


def _semantic_polish_segments_openai_pipeline(
    client: openai.OpenAI,
    seg_work: list[dict[str, Any]],
    subject: str,
    speech_language: str,
) -> tuple[list[dict[str, Any]], dict[str, int], bool]:
    if not seg_work or speech_language not in ("fr", "ar"):
        return seg_work, {}, False

    model = (os.getenv("TRANSCRIBE_SEMANTIC_POLISH_MODEL") or "gpt-4o-mini").strip()
    try:
        bs = int(float(os.getenv("TRANSCRIBE_SEMANTIC_POLISH_BATCH_SEGMENTS", "24")))
        bs = max(6, min(48, bs))
    except ValueError:
        bs = 24

    subj_snip = (subject or "").strip() or "General"
    fused_tail = ""

    totals = {"semantic_polish_prompt_tokens": 0, "semantic_polish_completion_tokens": 0, "semantic_polish_total_tokens": 0}
    altered = False

    for off in range(0, len(seg_work), bs):
        batch = seg_work[off : off + bs]
        part, tok_use, fused_tail = _semantic_polish_one_batch_openai(
            client, model, speech_language, subj_snip, fused_tail, batch
        )
        if tok_use:
            totals["semantic_polish_prompt_tokens"] += tok_use["prompt_tokens"]
            totals["semantic_polish_completion_tokens"] += tok_use["completion_tokens"]
            totals["semantic_polish_total_tokens"] += tok_use["total_tokens"]

        if part:
            for d in batch:
                ix = int(d["_poly_ix"])
                nv = part.get(ix)
                if nv is None:
                    continue
                ov = str(d.get("text") or "")
                if nv.strip() != ov.strip():
                    altered = True
                d["text"] = nv

    for d in seg_work:
        d.pop("_poly_ix", None)

    usage_out = totals if totals["semantic_polish_total_tokens"] else {}
    return seg_work, usage_out, altered


def _translate_segments_openai_batch(
    client: openai.OpenAI,
    model: str,
    target_label: str,
    speech_language: str,
    items: list[tuple[int, str, Optional[str], Optional[str]]],
) -> tuple[dict[int, str], Optional[dict[str, int]]]:
    """Traduit des fragments (IDs arbitraires) ; retourne {id -> texte}."""
    if not items:
        return {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    inp = [
        {
            "id": i,
            "detected_hint": dh or "",
            "hint_reason": hr or "",
            "text": txt[:4800],
        }
        for i, txt, dh, hr in items
    ]
    payload = json.dumps(inp, ensure_ascii=False)

    french_extra = ""
    arabic_extra = ""
    if speech_language == "fr":
        french_extra = (
            " Output MUST contain ZERO Han, Hiragana, Katakana, or Hangul characters. "
            "If fragments look like Whisper ASR noise (Japanese/Chinese/Korean glyphs), discard them "
            "or reconstruct the intended spoken French meaning from neighbouring context. "
            "Use only French punctuation and French orthography conventions."
        )
    elif speech_language == "ar":
        arabic_extra = (
            " Output MUST contain ZERO Han/Kana/Hangul; remove script noise. "
            "Express everything in coherent Modern Standard Arabic (Arabic script) unless the snippet is purely a Latin acronym unchanged."
        )

    system_rules = (
        f"You unify fragments from a transcribed lecture into natural {target_label}. "
        "Preserve plausible proper names (romanized briefly if needed). "
        "Keep any embedded [MM:SS] timestamp tokens exactly as-is if present inside a fragment."
        + french_extra
        + arabic_extra
        + " "
        + "hint_reason briefly explains why cleanup is needed when present (noise script, parasite language, tail English). "
        + "Return ONLY compact JSON "
        '{"translations":[{"id":<int>,"text":<string>}]} '
        + "same count and ids as input. If fragment is ALREADY in target language, return verbatim."
    )

    tok: Optional[dict[str, int]] = None
    try:
        comp = client.chat.completions.create(
            model=model,
            temperature=0.12,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_rules},
                {"role": "user", "content": f"Fragments JSON:\n{payload}"},
            ],
        )
    except Exception:
        logger.warning("TRANSCRIBE: mixed-lang Chat batch failed.", exc_info=True)
        return {i: t for i, t, _, __ in items}, None

    try:
        u = getattr(comp, "usage", None)
        if u is not None:
            tok = {
                "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
            }
    except Exception:
        tok = None

    txt = ""
    try:
        ch0 = comp.choices[0]
        txt = (ch0.message.content or "").strip()
    except Exception:
        logger.warning("TRANSCRIBE: mixed-lang Chat response sans contenu lisible.")
        return {i: t for i, t, _, __ in items}, tok

    out_map: dict[int, str] = {}
    try:
        data = json.loads(txt)
        arr = data.get("translations") or data.get("items") or []
        for row in arr:
            sid = row.get("id")
            out = row.get("text") or row.get("t")
            if sid is None or out is None:
                continue
            out_map[int(sid)] = str(out).strip("\n") if isinstance(out, str) else ""
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("TRANSCRIBE: mixed-lang JSON parse failed; extrait: %s...", txt[:200])

    for i, orig, _, __ in items:
        if i not in out_map or not (out_map.get(i) or "").strip():
            out_map[i] = orig

    return out_map, tok


def build_transcript_mixed_view_blocks(
    client: Optional[openai.OpenAI],
    segments: list,
    fallback_text: str,
    speech_language: str,
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    """
    Marqueurs + blocs « text » : plusieurs blocs peuvent représenter une même unité Whisper
    (traduction intra-segment français + anglais, ou scripts parasites).
    """
    structure = _timestamp_structure(segments, fallback_text)
    blocks: list[dict[str, Any]] = []

    if not structure:
        return blocks, None

    text_segs_ordered = _ordered_text_segments_for_timestamp_structure(segments)

    try:
        _min_mc = int(float(os.getenv("TRANSCRIBE_MIXED_LANG_DETECT_MIN_CHARS", "18")))
        min_clause_chars = max(8, min(320, _min_mc))
    except ValueError:
        min_clause_chars = 18

    translation_tasks: list[tuple[int, str, Optional[str], Optional[str]]] = []
    tid_counter = 0

    transient_text_blocks: list[dict[str, Any]] = []

    for kind, sid, blob in structure:
        if kind == "marker":
            blocks.append({"kind": "marker", "text": blob})
            continue
        if sid is None:
            continue
        spans = _mixed_spans_whisper_fragment(blob, speech_language, min_clause_chars)

        parent_seg = text_segs_ordered[sid] if isinstance(sid, int) and 0 <= sid < len(text_segs_ordered) else None
        whisper_rel = _whisper_reliability_payload(parent_seg) if parent_seg is not None else None

        transient_text_blocks.clear()
        for sp in spans:
            raw_t = sp.get("text") or ""
            fo = bool(sp.get("foreign"))
            dl_raw = sp.get("detected_lang") if fo else None
            dl_val = dl_raw if isinstance(dl_raw, str) else None
            rr = sp.get("reason") if fo else None

            blk: dict[str, Any] = {
                "kind": "text",
                "segment_index_whisper": int(sid),
                "original": raw_t,
                "display": raw_t,
                "translated": fo,
                "detected_lang": dl_val if fo else None,
                "detected_reason": rr if fo else None,
            }
            if whisper_rel is not None:
                blk["whisper_reliability"] = whisper_rel
            if fo:
                blk["_tid"] = tid_counter
                translation_tasks.append((tid_counter, raw_t, dl_val, rr if isinstance(rr, str) else None))
                tid_counter += 1
            transient_text_blocks.append(blk)
        blocks.extend(transient_text_blocks)

    translations: dict[int, str] = {}
    tm_usage: Optional[dict[str, int]] = None

    batch_size = max(12, min(45, int(float(os.getenv("TRANSCRIBE_MIXED_LANG_BATCH_MAX", "32")))))

    if translation_tasks and client is not None and _env_truthy("TRANSCRIBE_ENABLE_MIXED_LANG_VIEW", True):
        model = (os.getenv("TRANSCRIBE_MIXED_LANG_MODEL") or "gpt-4o-mini").strip()
        target = _mixed_lang_target_label(speech_language)
        p_sum = c_sum = t_sum = 0
        for off in range(0, len(translation_tasks), batch_size):
            chunk = translation_tasks[off : off + batch_size]
            part_map, tok = _translate_segments_openai_batch(client, model, target, speech_language, chunk)
            translations.update(part_map)
            if tok:
                p_sum += tok["prompt_tokens"]
                c_sum += tok["completion_tokens"]
                t_sum += tok["total_tokens"]
        if p_sum or c_sum:
            tm_usage = {
                "segment_translation_prompt_tokens": p_sum,
                "segment_translation_completion_tokens": c_sum,
                "segment_translation_total_tokens": t_sum,
            }

    for blk in blocks:
        t_i = blk.pop("_tid", None)
        if t_i is None:
            continue
        blk["display"] = translations.get(t_i, blk.get("original") or "")
        if blk.get("translated"):
            blk["display"] = _scrub_residual_cjk_from_display(blk.get("display"))

    meta: Optional[dict[str, Any]] = {"foreign_segments_detected": len(translation_tasks)}
    if tm_usage:
        meta["usage"] = dict(tm_usage)

    return blocks, meta


def _estimated_duration_seconds_from_file(path: str) -> Optional[float]:
    """Lecture métadonnées (TinyTag). Retour None si inconnu."""
    try:
        info = TinyTag.get(path)
        d = getattr(info, "duration", None)
        if d is None or d <= 0:
            return None
        return float(d)
    except Exception:
        return None


def _reject_if_duration_exceeds(seconds: float) -> None:
    if seconds <= MAX_DURATION_SECONDS:
        return
    h_limit = MAX_DURATION_SECONDS / 3600
    detail = (
        f"Durée trop longue ({seconds / 3600:.2f} h). "
        f"Maximum autorisé : {h_limit:.0f} h ({MAX_DURATION_SECONDS} s)."
    )
    raise HTTPException(status_code=400, detail=detail)


def _normalize_speech_language(raw: Optional[str]) -> str:
    code = (raw or "fr").strip().lower()[:16]
    if code in ("fr", "french", "français"):
        return "fr"
    if code in ("ar", "arabic", "arab"):
        return "ar"
    return "fr"


def _whisper_prompt(subject: str, speech_language: str) -> Optional[str]:
    """
    Texte optionnel passé à Whisper (`prompt` OpenAI).

    Par défaut : **aucun** prompt — les formulations du type « enregistrement de cours / français »
    peuvent malgré tout biaiser le modèle vers des phrases « type cours » ou des boucles répétitives
    sur fichiers longs, même quand l’utilisateur n’a rien saisi comme matière.

    - `TRANSCRIBE_WHISPER_STYLE_PROMPT=true` → réactive le court texte d’orientation fr/ar (hors thème).
    - `TRANSCRIBE_WHISPER_SUBJECT_IN_PROMPT=true` → ajoute une courte ligne « thème » (voir …_MAX_CHARS).

    Le champ matière du formulaire sert toujours à la génération de cours / polish, indépendamment de ce prompt.
    """
    bits: list[str] = []
    subj = (subject or "").strip()
    theme = ""
    if _env_truthy("TRANSCRIBE_WHISPER_SUBJECT_IN_PROMPT", False):
        try:
            theme_max = int(os.getenv("TRANSCRIBE_WHISPER_SUBJECT_MAX_CHARS", "42") or "42")
        except ValueError:
            theme_max = 42
        theme_max = max(8, min(120, theme_max))
        if subj and subj.lower() != "general":
            raw = subj.strip()
            if len(raw) <= theme_max:
                theme = raw
            else:
                theme = raw[: theme_max - 1].rstrip() + "…"

    style_on = _env_truthy("TRANSCRIBE_WHISPER_STYLE_PROMPT", False)

    if speech_language == "fr":
        if style_on:
            bits.append(
                "Enregistrement d’un cours oral. Langue dominante : français."
                " Courts passages en dialecte ou en langue régionale peuvent être présents en introduction."
            )
        if theme:
            bits.append(f"Thème / matière probable : {theme}")
    else:
        if style_on:
            bits.append("تسجيل لمحاضرة أو درس. اللغة المستهدفة للكتبة: العربية الفصحى حيثما أمكن.")
        if theme:
            bits.append(f"الموضوع المذكور: {theme}")

    txt = "\n".join(bits).strip()
    return txt or None


def _ndjson_line(ev: dict[str, Any]) -> bytes:
    return (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")


def _cleanup_transcription_tempfiles(
    tmp_path: Optional[str],
    processed_mp3: Optional[str],
    chunk_paths: Optional[list[str]] = None,
    *,
    preserve_tmp_path: bool = False,
) -> None:
    for path in chunk_paths or []:
        if not path:
            continue
        try:
            os.unlink(path)
        except OSError:
            pass
    for path in (tmp_path, processed_mp3):
        if not path:
            continue
        if preserve_tmp_path and path == tmp_path:
            continue
        try:
            os.unlink(path)
        except OSError:
            pass


def _call_whisper_transcription(
    client: openai.OpenAI,
    whisper_path: str,
    speech_language: str,
    whisper_prompt: Optional[str],
):
    with open(whisper_path, "rb") as audio_file:
        create_kw: dict[str, Any] = {
            "model": "whisper-1",
            "file": audio_file,
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
            "language": speech_language,
        }
        if whisper_prompt:
            create_kw["prompt"] = whisper_prompt
        return client.audio.transcriptions.create(**create_kw)


def _call_whisper_transcription_merged(
    client: openai.OpenAI,
    whisper_chunks: list[tuple[str, float]],
    speech_language: str,
    whisper_prompt: Optional[str],
) -> Any:
    """Un ou plusieurs appels Whisper ; fusion des segments avec décalage temporel."""
    if not whisper_chunks:
        raise ValueError("whisper_chunks vide")
    responses: list[Any] = []
    offsets: list[float] = []
    for path, off in whisper_chunks:
        responses.append(_call_whisper_transcription(client, path, speech_language, whisper_prompt))
        offsets.append(off)
    return _merge_whisper_verbose_responses(responses, offsets)


def _build_transcribe_success_payload(
    *,
    db: Session,
    _auth: Optional[User],
    client: openai.OpenAI,
    response,
    subject: str,
    speech_language: str,
    filename: Optional[str],
    audio_preprocess_applied: bool,
    whisper_chunk_count: int = 1,
) -> dict[str, Any]:
    segments_raw = getattr(response, "segments", None) or []
    seg_work = _segments_to_processing_dicts(segments_raw)

    semantic_polish_applied = False
    polish_usage_accum: dict[str, int] = {}
    polish_on = (
        _env_truthy("TRANSCRIBE_ENABLE_SEMANTIC_POLISH", False)
        and bool(seg_work)
        and speech_language in ("fr", "ar")
    )
    if polish_on:
        seg_work, polish_usage_accum, semantic_polish_applied = _semantic_polish_segments_openai_pipeline(
            client, seg_work, subject, speech_language
        )
    else:
        for _sd in seg_work:
            _sd.pop("_poly_ix", None)

    segments = seg_work if seg_work else segments_raw

    language = getattr(response, "language", None) or "unknown"
    api_text = (getattr(response, "text", None) or "").strip()

    verbatim = build_verbatim_transcript(segments, api_text)
    timestamped = build_timestamped_transcript(segments, api_text)
    if not timestamped:
        timestamped = verbatim

    if segments:
        last = segments[-1]
        end = last.get("end", 0) if isinstance(last, dict) else last.end
        duration_min = max(1, int(math.ceil(float(end) / 60)))
        duration_sec = float(end)
    else:
        duration_min = 0
        duration_sec = 0.0

    _reject_if_duration_exceeds(duration_sec)

    text_for_token_est = timestamped or verbatim
    est_tokens = estimate_tokens_from_chars(text_for_token_est)
    whisper_usd_audio = whisper_provider_usd(duration_sec)

    transcript_mixed_view: Optional[dict[str, Any]] = None
    mixed_usage_update: dict[str, Any] = {}
    if _env_truthy("TRANSCRIBE_ENABLE_MIXED_LANG_VIEW", True):
        m_blocks, mixed_meta = build_transcript_mixed_view_blocks(client, segments, api_text, speech_language)
        _parts_m: list[str] = []
        for b in m_blocks:
            if b.get("kind") == "text":
                _parts_m.append(str(b.get("display") or b.get("original") or ""))
            else:
                _parts_m.append(str(b.get("text") or ""))
        plain_mixed = "".join(_parts_m)

        hl_blocks = sum(
            1
            for b in m_blocks
            if b.get("kind") == "text"
            and isinstance(b.get("whisper_reliability"), dict)
            and bool((b.get("whisper_reliability") or {}).get("high_reliability"))
        )

        transcript_mixed_view = {
            "blocks": m_blocks,
            "plain_text": plain_mixed,
            "foreign_segment_count": int((mixed_meta or {}).get("foreign_segments_detected", 0)),
            "high_reliability_block_count": hl_blocks,
        }
        mix_u = (mixed_meta or {}).get("usage")
        mixed_usage_update = dict(mix_u) if isinstance(mix_u, dict) else mixed_usage_update

    chat_prompt_tot = int(polish_usage_accum.get("semantic_polish_prompt_tokens") or 0) + int(
        mixed_usage_update.get("segment_translation_prompt_tokens") or 0,
    )
    chat_comp_tot = int(polish_usage_accum.get("semantic_polish_completion_tokens") or 0) + int(
        mixed_usage_update.get("segment_translation_completion_tokens") or 0,
    )
    chat_usd_est = openai_transcribe_chat_provider_usd(chat_prompt_tot, chat_comp_tot)
    provider_usd_total = whisper_usd_audio + chat_usd_est
    billed_transcribe_total_mru = transcribe_aggregate_billed_mru(provider_usd_total)

    payload: dict[str, Any] = {
        "transcript": verbatim,
        "timestamped_transcript": timestamped,
        "speech_language": speech_language,
        "semantic_polish_applied": semantic_polish_applied,
        "audio_preprocess_applied": audio_preprocess_applied,
        "whisper_auto_chunked": whisper_chunk_count > 1,
        "whisper_chunk_count": whisper_chunk_count,
        "transcript_mixed_view": transcript_mixed_view,
        "language": language,
        "duration_minutes": duration_min,
        "duration_seconds": duration_sec,
        "subject": subject,
        "filename": filename,
        "word_count": len(verbatim.split()) if verbatim else 0,
        "usage": {
            "transcript_estimated_tokens": est_tokens,
            "whisper_duration_seconds": round(duration_sec, 3),
            "provider_usd_whisper_audio": round(whisper_usd_audio, 8),
            "provider_usd_openai_chat_est": round(chat_usd_est, 10),
            "provider_usd_transcribe_total": round(provider_usd_total, 10),
            "openai_chat_prompt_tokens_used": chat_prompt_tot,
            "openai_chat_completion_tokens_used": chat_comp_tot,
            # Facturation utilisateur agrégée (pas seulement Whisper) pour compat ancien champ :
            "billed_mru_whisper": round(billed_transcribe_total_mru, 6),
            "billed_mru_transcription_total": round(billed_transcribe_total_mru, 6),
        },
    }
    if polish_usage_accum:
        payload["usage"].update(polish_usage_accum)
    if transcript_mixed_view is not None and mixed_usage_update:
        payload["usage"].update(mixed_usage_update)
    charge_units = billed_mru_to_wallet_units_debit(billed_transcribe_total_mru)
    new_bal, charged = debit_credits(db, _auth, charge_units)
    if new_bal is not None:
        payload["wallet"] = {
            "balance_units": new_bal,
            "spent_units": charged,
            "balance_mru": wallet_units_to_mru_display(new_bal),
            "spent_mru_this_request": wallet_units_to_mru_display(charged),
        }
        payload["credits"] = payload["wallet"]

    return payload


def _reject_disallowed_hint_content_type(content_type_hint: Optional[str]) -> None:
    """Refus MIME évident hors audio lorsque un type est renseigné (upload navigateur ou en-tête multipart)."""
    ct = (content_type_hint or "").split(";")[0].strip().lower()
    if not ct or ct == "application/octet-stream":
        return
    # AAC/M4A dans un conteneur ISO MP4 est souvent envoyé en video/mp4 ou audio/mp4 par le navigateur.
    if ct in ("video/mp4", "audio/mp4", "audio/x-m4a"):
        return
    if ct.startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Les vidéos ne sont pas acceptées : exporte ou extrais uniquement la piste audio "
                "(MP3, M4A, WAV, FLAC, etc.), puis réessaie."
            ),
        )
    if ct.startswith("image/") or ct in ("application/pdf", "application/x-pdf"):
        raise HTTPException(
            status_code=400,
            detail="Seuls les fichiers audio sont acceptés (pas PDF ni image).",
        )


def _reject_disallowed_media_type(upload: UploadFile) -> None:
    """Refus explicite des types évidents hors audio envoi multipart."""
    _reject_disallowed_hint_content_type(upload.content_type)


_ALLOWED_EXT_HELP = ", ".join(sorted(ext.lstrip(".").upper() for ext in ALLOWED_EXTENSIONS))


def _http_exc_detail_as_message(detail: Any) -> str:
    if isinstance(detail, str):
        s = detail.strip()
        return s if s else "Requête refusée."
    return str(detail).strip() or "Requête refusée."


def _materialize_transcribe_context_from_bytes(
    content: bytes,
    ext: str,
    subject: str,
    speech_language_in: str,
    client_content_type_hint: Optional[str],
) -> dict[str, Any]:
    """
    Écrit le média sur disque, estime la durée, prépare les morceaux Whisper (FFmpeg possible — **bloquant**).
    À exécuter dans un thread pour ne pas geler la boucle asyncio pendant les longues conversions.
    """
    _reject_disallowed_hint_content_type(client_content_type_hint)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="La clé technique de transcription est manquante sur le serveur.",
        )

    speech_language = _normalize_speech_language(speech_language_in)
    whisper_prompt = _whisper_prompt(subject, speech_language)
    client = openai.OpenAI(api_key=api_key)

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    estimated = _estimated_duration_seconds_from_file(tmp_path)
    if estimated is not None:
        _reject_if_duration_exceeds(estimated)

    whisper_chunks, chunk_temp_paths, audio_preprocess_single = _prepare_whisper_chunks(tmp_path, estimated)
    processed_mp3 = whisper_chunks[0][0] if len(whisper_chunks) == 1 and audio_preprocess_single else None

    return {
        "client": client,
        "tmp_path": tmp_path,
        "processed_mp3": processed_mp3,
        "chunk_temp_paths": chunk_temp_paths,
        "whisper_chunks": whisper_chunks,
        "whisper_prompt": whisper_prompt,
        "speech_language": speech_language,
        "estimated": estimated,
        "audio_preprocess_single_file": audio_preprocess_single,
    }


async def _load_transcribe_context(
    file: UploadFile,
    subject: str,
    speech_language_in: str,
) -> dict[str, Any]:
    _reject_disallowed_media_type(file)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Extension non audio ou non supportée (« {ext or 'sans extension'} »). "
                f"Formats acceptés : {_ALLOWED_EXT_HELP}."
            ),
        )

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Fichier trop volumineux ({size_mb:.1f} Mo). Taille maximale : {MAX_SIZE_MB} Mo "
                "(paramètre TRANSCRIBE_MAX_MB sur le serveur)."
            ),
        )

    return _materialize_transcribe_context_from_bytes(
        content,
        ext,
        subject,
        speech_language_in,
        file.content_type,
    )


def _load_transcribe_context_from_path(
    filesystem_path: str,
    original_filename: str,
    subject: str,
    speech_language_in: str,
    *,
    hint_content_type: Optional[str] = None,
) -> dict[str, Any]:
    """Comme `_load_transcribe_context`, mais fichier déjà sur disque (jobs en arrière-plan)."""
    _reject_disallowed_hint_content_type(hint_content_type)
    ext = os.path.splitext(original_filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Extension non audio ou non supportée (« {ext or 'sans extension'} »). "
                f"Formats acceptés : {_ALLOWED_EXT_HELP}."
            ),
        )
    rp = os.path.realpath(filesystem_path)
    if not os.path.isfile(rp):
        raise HTTPException(status_code=400, detail="Fichier audio introuvable sur le serveur.")
    try:
        size_mb = os.path.getsize(rp) / (1024 * 1024)
    except OSError:
        raise HTTPException(status_code=400, detail="Impossible de lire la taille du fichier.") from None
    if size_mb > MAX_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Fichier trop volumineux ({size_mb:.1f} Mo). Taille maximale : {MAX_SIZE_MB} Mo "
                "(paramètre TRANSCRIBE_MAX_MB sur le serveur)."
            ),
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="La clé technique de transcription est manquante sur le serveur.",
        )

    speech_language = _normalize_speech_language(speech_language_in)
    whisper_prompt = _whisper_prompt(subject, speech_language)
    client = openai.OpenAI(api_key=api_key)
    tmp_path = rp

    estimated = _estimated_duration_seconds_from_file(tmp_path)
    if estimated is not None:
        _reject_if_duration_exceeds(estimated)

    whisper_chunks, chunk_temp_paths, audio_preprocess_single = _prepare_whisper_chunks(tmp_path, estimated)
    processed_mp3 = whisper_chunks[0][0] if len(whisper_chunks) == 1 and audio_preprocess_single else None

    return {
        "client": client,
        "tmp_path": tmp_path,
        "processed_mp3": processed_mp3,
        "chunk_temp_paths": chunk_temp_paths,
        "whisper_chunks": whisper_chunks,
        "whisper_prompt": whisper_prompt,
        "speech_language": speech_language,
        "estimated": estimated,
        "audio_preprocess_single_file": audio_preprocess_single,
    }


@router.post("/transcribe")
async def transcribe_audio(
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
    file: UploadFile = File(...),
    subject: str = Form(default="General"),
    speech_language: str = Form(default="fr"),
):
    ctx = await _load_transcribe_context(file, subject, speech_language)
    try:
        response = _call_whisper_transcription_merged(
            ctx["client"],
            ctx["whisper_chunks"],
            ctx["speech_language"],
            ctx["whisper_prompt"],
        )
        n_chunks = len(ctx["whisper_chunks"])
        payload = _build_transcribe_success_payload(
            db=db,
            _auth=_auth,
            client=ctx["client"],
            response=response,
            subject=subject,
            speech_language=ctx["speech_language"],
            filename=file.filename,
            audio_preprocess_applied=bool(ctx.get("audio_preprocess_single_file")),
            whisper_chunk_count=n_chunks,
        )
        return JSONResponse(payload)
    except openai.AuthenticationError:
        logger.error("OpenAI AuthenticationError sur /transcribe.", exc_info=False)
        raise HTTPException(
            status_code=500,
            detail="Service de transcription indisponible (configuration serveur). Réessaie plus tard.",
        )
    except openai.RateLimitError:
        logger.warning("OpenAI RateLimitError sur /transcribe.")
        raise HTTPException(
            status_code=429,
            detail="Trop de demandes en ce moment — réessaie dans environ une minute.",
        ) from None
    except openai.APIConnectionError:
        logger.warning("OpenAI APIConnectionError sur /transcribe.", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Connexion au service de transcription impossible. Réessaie plus tard.",
        ) from None
    except openai.APIStatusError as e:
        resp_text = ""
        try:
            r = getattr(e, "response", None)
            if r is not None:
                resp_text = (getattr(r, "text", None) or "")[:900]
        except Exception:
            resp_text = ""
        low = resp_text.lower()
        sc = getattr(e, "status_code", None) or 0
        logger.warning(
            "OpenAI APIStatusError transcription status=%s body=%s...",
            sc,
            resp_text[:240],
        )
        detail = "La transcription de ce fichier a été refusée. Vérifie le format (MP3 ou M4A recommandés) ou réessaie."
        if sc in (413, 400):
            detail = "Fichier refusé (souvent : segment trop lourd ou format peu supporté). Réduis la taille ou réencode en MP3/M4A."
        elif "size" in low or ("file" in low and "large" in low) or ("25" in resp_text.lower() and "mb" in low):
            detail = "Un segment du fichier est encore trop volumineux. Réduis la taille totale ou exporte en MP3/M4A plus léger."
        elif sc == 415 or "unsupported" in low or "mime" in low:
            detail = "Format audio peu reconnu — réencode en MP3 ou M4A (audio uniquement)."
        raise HTTPException(status_code=502 if sc >= 500 else 400 if sc < 500 else 502, detail=detail) from None
    except openai.OpenAIError:
        logger.exception("OpenAI erreur générique transcription", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="La transcription a échoué. Vérifie le fichier puis réessaie ; si ça persiste, réessaie plus tard.",
        )
    finally:
        _cleanup_transcription_tempfiles(
            ctx["tmp_path"],
            ctx["processed_mp3"],
            ctx.get("chunk_temp_paths"),
        )


def _ndjson_detail_for_openai_status(e: openai.APIStatusError) -> str:
    """Message affiché à l’utilisateur final (pas de nom de fournisseur ni jargon interne)."""
    resp_text = ""
    try:
        r = getattr(e, "response", None)
        if r is not None:
            resp_text = (getattr(r, "text", None) or "")[:900]
    except Exception:
        resp_text = ""
    low = resp_text.lower()
    sc = getattr(e, "status_code", None) or 0
    detail = "La transcription de ce fichier a été refusée. Vérifie le format (MP3 ou M4A recommandés) ou réessaie."
    if sc in (413, 400):
        detail = "Fichier refusé (souvent : segment trop lourd ou format peu supporté). Réduis la taille ou réencode en MP3/M4A."
    elif "size" in low or ("file" in low and "large" in low) or ("25" in resp_text.lower() and "mb" in low):
        detail = "Un segment du fichier est encore trop volumineux. Réduis la taille totale ou exporte en MP3/M4A plus léger."
    elif sc == 415 or "unsupported" in low or "mime" in low:
        detail = "Format audio peu reconnu — réencode en MP3 ou M4A (audio uniquement)."
    return detail


async def iterate_transcription_events(
    *,
    ctx: dict[str, Any],
    db: Session,
    _auth: Optional[User],
    subject: str,
    display_filename: Optional[str],
    whisper_rt: float,
) -> AsyncIterator[dict[str, Any]]:
    """Événements NDJSON (dict) — partagés par `/transcribe-stream` et les jobs persistants."""
    yield {
        "type": "status",
        "phase": "received",
        "message": "Fichier reçu sur le serveur.",
        "server_frac": 0.03,
    }

    estimated = ctx["estimated"]
    prep_msg = "Préparation de l’audio sur le serveur (normalisation, découpage si nécessaire)…"
    yield {
        "type": "status",
        "phase": "preprocessing",
        "message": prep_msg,
        "server_frac": 0.09,
        "audio_duration_estimate_sec": round(estimated, 1) if estimated is not None else None,
    }

    chunks = ctx["whisper_chunks"]
    n_chunks = len(chunks)
    responses_acc: list[Any] = []
    offsets_acc: list[float] = []
    total_est = estimated if estimated is not None and estimated > 0 else 120.0
    expected_whisper_total = max(25.0, total_est * whisper_rt * (1.05 + 0.08 * max(0, n_chunks - 1)))
    t_whisper_start = time.monotonic()

    for idx, (part_path, t_off) in enumerate(chunks):
        if n_chunks > 1:
            yield {
                "type": "status",
                "phase": "whisper_chunk",
                "message": f"Transcription en cours — partie {idx + 1}/{n_chunks}…",
                "server_frac": round(0.10 + 0.50 * (idx / max(1, n_chunks)), 4),
                "whisper_chunk_index": idx + 1,
                "whisper_chunk_total": n_chunks,
            }

        task = asyncio.create_task(
            asyncio.to_thread(
                _call_whisper_transcription,
                ctx["client"],
                part_path,
                ctx["speech_language"],
                ctx["whisper_prompt"],
            ),
        )
        chunk_est = max(20.0, (total_est / max(1, n_chunks)) * whisper_rt * 1.2)
        t_chunk_start = time.monotonic()
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.52, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                break
            elapsed_wh = time.monotonic() - t_whisper_start
            sub_total = min(0.95, elapsed_wh / expected_whisper_total)
            elapsed_chunk = time.monotonic() - t_chunk_start
            sub_chunk = min(0.98, elapsed_chunk / chunk_est)
            server_frac = 0.10 + 0.52 * ((idx + sub_chunk) / max(1, n_chunks))
            server_frac = min(0.64, max(0.10, server_frac))
            yield {
                "type": "status",
                "phase": "whisper",
                "message": (
                    f"Écoute et transcription — partie {idx + 1}/{n_chunks}…"
                    if n_chunks > 1
                    else "Écoute et transcription de l’audio…"
                ),
                "estimate_note": (
                    f"Temps restant estimé ≈ {expected_whisper_total:.0f}s."
                    if n_chunks == 1
                    else f"Partie {idx + 1}/{n_chunks} · durée totale estimée ≈ {expected_whisper_total:.0f}s."
                ),
                "server_frac": round(server_frac, 4),
                "whisper_elapsed_sec": round(elapsed_wh, 1),
                "whisper_expected_sec": round(expected_whisper_total, 1),
                "whisper_subprogress": round(sub_total if n_chunks == 1 else sub_chunk, 3),
            }

        try:
            r_part = task.result()
        except openai.AuthenticationError:
            logger.error("OpenAI AuthenticationError sur /transcribe-stream.", exc_info=False)
            yield {"type": "error", "detail": "Service de transcription indisponible (configuration serveur). Réessaie plus tard."}
            return
        except openai.RateLimitError:
            logger.warning("OpenAI RateLimitError sur /transcribe-stream.")
            yield {"type": "error", "detail": "Trop de demandes en ce moment — réessaie dans environ une minute."}
            return
        except openai.APIConnectionError:
            logger.warning("OpenAI APIConnectionError sur /transcribe-stream.", exc_info=True)
            yield {"type": "error", "detail": "Connexion au service de transcription impossible. Réessaie plus tard."}
            return
        except openai.APIStatusError as e:
            sc = getattr(e, "status_code", None) or 0
            logger.warning("OpenAI APIStatusError transcription stream status=%s", sc)
            yield {"type": "error", "detail": _ndjson_detail_for_openai_status(e)}
            return
        except openai.OpenAIError as e:
            logger.exception("OpenAI erreur générique transcription stream", exc_info=True)
            yield {
                "type": "error",
                "detail": "La transcription a échoué — vérifie le fichier et réessaie plus tard.",
            }
            return

        responses_acc.append(r_part)
        offsets_acc.append(t_off)

    response = _merge_whisper_verbose_responses(responses_acc, offsets_acc)

    yield {"type": "status", "phase": "whisper_complete", "message": "Transcription audio terminée.", "server_frac": 0.68}

    api_text_preview = (getattr(response, "text", None) or "").strip()
    if len(api_text_preview) > 1600:
        api_text_preview = api_text_preview[:1600].rstrip() + "…"
    yield {"type": "preview", "text": api_text_preview, "server_frac": 0.72, "message": "Aperçu du texte — finalisation en cours."}

    yield {"type": "status", "phase": "post_process", "message": "Repères temporels, langue et options d’affichage…", "server_frac": 0.80}

    try:
        payload = _build_transcribe_success_payload(
            db=db,
            _auth=_auth,
            client=ctx["client"],
            response=response,
            subject=subject,
            speech_language=ctx["speech_language"],
            filename=display_filename,
            audio_preprocess_applied=bool(ctx.get("audio_preprocess_single_file")),
            whisper_chunk_count=len(ctx["whisper_chunks"]),
        )
    except openai.AuthenticationError:
        yield {"type": "error", "detail": "Impossible de finaliser la transcription (service indisponible). Réessaie plus tard."}
        return
    except openai.RateLimitError:
        yield {"type": "error", "detail": "Limite d’usage atteinte pendant la finalisation — réessaie dans une minute."}
        return
    except openai.APIConnectionError:
        yield {"type": "error", "detail": "Connexion impossible pendant la finalisation — réessaie plus tard."}
        return
    except openai.APIStatusError as e:
        yield {"type": "error", "detail": _ndjson_detail_for_openai_status(e)}
        return
    except openai.OpenAIError:
        yield {"type": "error", "detail": "Erreur lors de la finalisation — réessaie dans un instant."}
        return
    except HTTPException as http_exc:
        raw_d = http_exc.detail
        if isinstance(raw_d, str):
            hx_msg = raw_d.strip() or "Requête refusée."
        else:
            hx_msg = "Requête refusée lors de la finalisation."
        yield {"type": "error", "detail": hx_msg}
        return

    yield {"type": "done", "server_frac": 1.0, "result": payload}


@router.post("/transcribe-stream")
async def transcribe_audio_stream(
    db: Annotated[Session, Depends(get_db)],
    _auth: Annotated[Optional[User], Depends(require_wallet_user)],
    file: UploadFile = File(...),
    subject: str = Form(default="General"),
    speech_language: str = Form(default="fr"),
):
    """NDJSON lignes `{type,...}` puis `done` avec le même `result` que `/transcribe`."""
    whisper_rt = float(os.getenv("WHISPER_PROGRESS_RT_FACTOR", "0.5"))
    prep_heartbeat_sec = float(os.getenv("TRANSCRIBE_STREAM_PREP_HEARTBEAT_SEC", "12"))

    async def event_iter() -> AsyncIterator[bytes]:
        ctx: Optional[dict[str, Any]] = None
        try:
            # Octets tout de suite : sans cela, FFmpeg sur ~1 h peut dépasser le délai lecture du reverse‑proxy
            # (souvent 60 s) avant le premier octet de réponse → 502 / message générique côté navigateur.
            yield _ndjson_line(
                {
                    "type": "status",
                    "phase": "accepted",
                    "message": (
                        "Requête acceptée — préparation du média sur le serveur "
                        "(plusieurs minutes possibles pour les fichiers longs)."
                    ),
                    "server_frac": 0.02,
                },
            )

            _reject_disallowed_media_type(file)
            ext = os.path.splitext(file.filename or "")[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                yield _ndjson_line(
                    {
                        "type": "error",
                        "detail": (
                            f"Extension non audio ou non supportée (« {ext or 'sans extension'} »). "
                            f"Formats acceptés : {_ALLOWED_EXT_HELP}."
                        ),
                    },
                )
                return

            content = await file.read()
            size_mb = len(content) / (1024 * 1024)
            if size_mb > MAX_SIZE_MB:
                yield _ndjson_line(
                    {
                        "type": "error",
                        "detail": (
                            f"Fichier trop volumineux ({size_mb:.1f} Mo). Taille maximale : {MAX_SIZE_MB} Mo "
                            "(paramètre TRANSCRIBE_MAX_MB sur le serveur)."
                        ),
                    },
                )
                return

            yield _ndjson_line(
                {
                    "type": "status",
                    "phase": "preprocessing",
                    "message": "Préparation de l’audio — merci de garder cet onglet ouvert…",
                    "server_frac": 0.06,
                },
            )

            hint_ct = file.content_type
            materialize_task = asyncio.create_task(
                asyncio.to_thread(
                    _materialize_transcribe_context_from_bytes,
                    content,
                    ext,
                    subject,
                    speech_language,
                    hint_ct,
                ),
            )
            hb_timeout = max(4.0, prep_heartbeat_sec)
            while not materialize_task.done():
                await asyncio.wait(
                    {materialize_task},
                    timeout=hb_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if materialize_task.done():
                    break
                yield _ndjson_line(
                    {
                        "type": "status",
                        "phase": "preprocessing",
                        "message": "Analyse de l’audio toujours en cours sur le serveur…",
                        "server_frac": 0.07,
                    },
                )

            try:
                ctx = materialize_task.result()
            except HTTPException as e:
                yield _ndjson_line({"type": "error", "detail": _http_exc_detail_as_message(e.detail)})
                return

            async for ev in iterate_transcription_events(
                ctx=ctx,
                db=db,
                _auth=_auth,
                subject=subject,
                display_filename=file.filename,
                whisper_rt=whisper_rt,
            ):
                yield _ndjson_line(ev)
        except HTTPException as e:
            yield _ndjson_line({"type": "error", "detail": _http_exc_detail_as_message(e.detail)})
        except Exception:
            logger.exception("TRANSCRIBE-STREAM: erreur non gérée pendant le flux NDJSON")
            yield _ndjson_line(
                {
                    "type": "error",
                    "detail": (
                        "Échec inattendu pendant la transcription. Réessaie plus tard "
                        "ou contacte l’administrateur si le problème continue."
                    ),
                },
            )
        finally:
            if ctx is not None:
                _cleanup_transcription_tempfiles(
                    ctx.get("tmp_path"),
                    ctx.get("processed_mp3"),
                    ctx.get("chunk_temp_paths"),
                )

    return StreamingResponse(
        event_iter(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            # Évite souvent la mise en buffer côté Nginx / reverse‑proxy pendant le NDJSON :
            "X-Accel-Buffering": "no",
        },
    )
