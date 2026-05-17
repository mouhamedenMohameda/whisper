"""Détecte les segments Whisper pathologiques et les remplace par un marqueur ``[inaudible]``.

Pourquoi ce module existe :
  Whisper produit naturellement des segments avec leurs propres métriques de qualité
  (avg_logprob, compression_ratio, no_speech_prob). Sur un audio mixte (parties propres +
  parties bruitées), certains segments sont fiables, d'autres sont des hallucinations.

  Au lieu de rejeter tout le fichier, on traite chaque segment **indépendamment** :
    - Bon segment → texte conservé tel quel
    - Pathologique → remplacé par ``[inaudible MM:SS-MM:SS]``

  Le LLM Groq (génération de fiche) voit ces marqueurs et sait qu'il ne doit PAS inventer
  de contenu pour ces plages. La fiche couvre uniquement les passages fiables, avec mention
  explicite des trous.

Détecteurs de pathologie par segment :
  1. **Métriques Whisper** (les + fiables) :
     - ``avg_logprob`` < seuil : faible probabilité moyenne par token
     - ``compression_ratio`` > seuil : compression élevée = boucle/répétition
     - ``no_speech_prob`` > seuil : Whisper pense que c'est du silence
  2. **Patterns texte** (filet de sécurité) :
     - Phrases-types d'hallucination (Amara.org, fillers YouTube...)
     - Répétition de n-grams (3+ fois la même séquence de mots)
     - Très peu de mots uniques (vocabulary diversity)

Le module retourne :
  - ``cleaned_segments`` : segments avec texte remplacé pour les pathologiques
  - ``inaudible_ranges`` : liste de (start_sec, end_sec) marqués inaudibles
  - ``usable_duration_sec`` : durée totale des segments conservés
  - ``inaudible_duration_sec`` : durée totale marquée
  - ``stats`` : compteurs détaillés (pour debug/monitoring)

Aucune dépendance externe. Sûr : tout passage du texte est conservé, on n'ajoute que des
marqueurs entre crochets — facilement détectables côté Groq prompt.

Désactivable via ``TRANSCRIBE_SEGMENT_CLEANER_ENABLED=false``.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from typing import Any, Optional

logger = logging.getLogger(__name__)


# === Seuils Whisper (alignés avec _whisper_reliability_payload dans transcribe.py) ===
# avg_logprob : Whisper considère < -1.0 comme dégradé (cf. doc OpenAI).
_AVG_LOGPROB_BAD = -1.0
# compression_ratio : > 2.4 indique boucle/répétition (OpenAI default).
_COMPRESSION_RATIO_BAD = 2.4
# no_speech_prob : > 0.6 → Whisper pense que c'est du silence/bruit.
_NO_SPEECH_BAD = 0.6


# Patterns d'hallucination Whisper bien connus (en plus des regex de asr_corrections.json).
# On match en lowercase, normalisé. Si le texte du segment EST quasi exclusivement un de ces patterns
# → segment marqué inaudible.
_HALLUCINATION_PATTERNS = [
    r"sous[- ]titres? r[ée]alis[ée]?s?[\s\S]{0,40}amara\.org",
    r"sous[- ]titres? r[ée]alis[ée]?s?[\s\S]{0,40}sous[- ]titreur",
    r"merci d'avoir regard[ée]",
    r"merci\s+\.?\s*$",
    r"❤️\s*par\s*soustitreur\.com",
    r"abonnez[- ]vous\s+à\s+(?:ma|notre|la)\s+cha[iî]ne",
    r"like\s+et\s+abonne[- ]?toi",
    r"^\s*\.\s*$",  # juste un point — Whisper renvoie ça quand il ne comprend rien
    r"^\s*(?:euh\s*\.?\s*){3,}$",  # boucle de "euh euh euh"
]
_HALL_RE = re.compile(r"(?:" + r"|".join(_HALLUCINATION_PATTERNS) + r")", re.IGNORECASE)


def is_enabled() -> bool:
    return (os.getenv("TRANSCRIBE_SEGMENT_CLEANER_ENABLED", "true").strip().lower()
            in ("1", "true", "yes", "on"))


def _thresholds() -> dict[str, float]:
    """Seuils ajustables via env vars. Tous facultatifs."""

    def _f(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)) or default)
        except ValueError:
            return default

    return {
        "avg_logprob_bad": _f("TRANSCRIBE_CLEAN_LOGPROB_BAD", _AVG_LOGPROB_BAD),
        "compression_bad": _f("TRANSCRIBE_CLEAN_COMPRESSION_BAD", _COMPRESSION_RATIO_BAD),
        "no_speech_bad": _f("TRANSCRIBE_CLEAN_NO_SPEECH_BAD", _NO_SPEECH_BAD),
        # Diversité lexicale minimum : ratio mots_uniques/total. Sous ce seuil = forte répétition.
        "min_lexical_diversity": _f("TRANSCRIBE_CLEAN_MIN_DIVERSITY", 0.25),
        # Pour la détection de répétition par n-gram : si même 3-gram apparaît ≥ N fois dans un segment.
        "max_ngram_repeat": _f("TRANSCRIBE_CLEAN_MAX_NGRAM_REPEAT", 3.0),
    }


def _seg_field(seg: Any, key: str) -> Any:
    if isinstance(seg, dict):
        return seg.get(key)
    return getattr(seg, key, None)


def _seg_float(seg: Any, key: str) -> Optional[float]:
    v = _seg_field(seg, key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _seg_text(seg: Any) -> str:
    t = _seg_field(seg, "text")
    if not isinstance(t, str):
        return ""
    return t


def _seg_start(seg: Any) -> float:
    v = _seg_float(seg, "start")
    return v if v is not None else 0.0


def _seg_end(seg: Any) -> float:
    v = _seg_float(seg, "end")
    return v if v is not None else 0.0


def _format_timestamp(seconds: float) -> str:
    """Secondes → ``MM:SS`` ou ``HH:MM:SS``."""
    try:
        s = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "00:00"
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:02d}:{ss:02d}"


def _is_pathological_by_metrics(seg: Any, th: dict[str, float]) -> Optional[str]:
    """Retourne une raison courte si le segment a une métrique Whisper hors seuil, sinon None."""
    alp = _seg_float(seg, "avg_logprob")
    cr = _seg_float(seg, "compression_ratio")
    nsp = _seg_float(seg, "no_speech_prob")

    if alp is not None and alp < th["avg_logprob_bad"]:
        return f"avg_logprob={alp:.2f}"
    if cr is not None and cr > th["compression_bad"]:
        return f"compression_ratio={cr:.2f}"
    if nsp is not None and nsp > th["no_speech_bad"]:
        return f"no_speech_prob={nsp:.2f}"
    return None


def _is_pathological_by_text(text: str, th: dict[str, float]) -> Optional[str]:
    """Détecte les pathologies de texte (hallucinations connues, boucles n-gram, faible diversité)."""
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None

    # 1) Match d'un pattern d'hallucination connu sur la majorité du segment
    hall = _HALL_RE.search(cleaned)
    if hall and len(hall.group(0)) >= 0.5 * len(cleaned):
        return f"hallucination_pattern: {hall.group(0)[:40]!r}"

    # 2) Diversité lexicale (sur segments assez longs pour que ce soit significatif)
    words = re.findall(r"\b\w+\b", cleaned.lower())
    if len(words) >= 15:
        diversity = len(set(words)) / len(words)
        if diversity < th["min_lexical_diversity"]:
            return f"low_diversity={diversity:.2f}"

    # 3) Répétition n-gram (3-gram qui revient max_ngram_repeat fois ou plus)
    if len(words) >= 12:
        trigrams = [tuple(words[i:i + 3]) for i in range(len(words) - 2)]
        if trigrams:
            top = Counter(trigrams).most_common(1)[0]
            if top[1] >= int(th["max_ngram_repeat"]):
                return f"ngram_repeat={top[1]} {' '.join(top[0])!r}"

    return None


def clean_segments(
    segments: list[Any],
    *,
    inaudible_label: str = "inaudible",
) -> dict[str, Any]:
    """Analyse chaque segment et remplace ceux pathologiques par ``[inaudible MM:SS-MM:SS]``.

    Args:
        segments: liste de segments Whisper (dicts ou objets verbose_json), tels que renvoyés
                  par l'API OpenAI ou par whisper local.
        inaudible_label: mot à mettre dans le marqueur (FR par défaut ; passer "غير مسموع" pour AR).

    Returns:
        Dict avec :
          - ``cleaned_segments`` : copies des segments d'origine, avec ``text`` remplacé pour les
            pathologiques (le reste des métriques est préservé pour traçabilité)
          - ``inaudible_ranges`` : liste de dicts ``{start_sec, end_sec, reason, original_text_preview}``
          - ``usable_duration_sec`` : somme des durées des segments conservés
          - ``inaudible_duration_sec`` : somme des durées marquées
          - ``total_duration_sec`` : durée totale (segments inclus)
          - ``stats`` : compteurs pour debug
    """
    if not is_enabled() or not segments:
        return {
            "cleaned_segments": list(segments or []),
            "inaudible_ranges": [],
            "usable_duration_sec": _total_duration(segments),
            "inaudible_duration_sec": 0.0,
            "total_duration_sec": _total_duration(segments),
            "stats": {"by_metrics": 0, "by_text": 0, "kept": len(segments or [])},
        }

    th = _thresholds()
    cleaned: list[Any] = []
    inaudible_ranges: list[dict[str, Any]] = []
    usable = 0.0
    inaudible_dur = 0.0
    total = 0.0
    n_by_metrics = 0
    n_by_text = 0
    n_kept = 0

    # Tri chronologique pour des fusions propres + timestamps cohérents dans les marqueurs.
    ordered = sorted(segments, key=_seg_start)

    for seg in ordered:
        start = _seg_start(seg)
        end = _seg_end(seg)
        dur = max(0.0, end - start)
        total += dur
        text = _seg_text(seg)

        reason_metrics = _is_pathological_by_metrics(seg, th)
        reason_text = _is_pathological_by_text(text, th) if not reason_metrics else None
        reason = reason_metrics or reason_text

        if reason is None:
            cleaned.append(seg)
            usable += dur
            n_kept += 1
            continue

        # Remplace le texte par un marqueur, on conserve les métadonnées segment pour cohérence
        # downstream (asr_passages_annotated, mixed view, etc.).
        marker = f"[{inaudible_label} {_format_timestamp(start)}-{_format_timestamp(end)}]"

        if isinstance(seg, dict):
            new_seg = dict(seg)
            new_seg["text"] = " " + marker + " "
            new_seg["_cleaner_marked_inaudible"] = True
            new_seg["_cleaner_reason"] = reason
            new_seg["_cleaner_original_text"] = (text or "")[:200]
            cleaned.append(new_seg)
        else:
            # Objet (SimpleNamespace ou Pydantic). On crée un dict normalisé pour ne pas planter
            # sur l'immutabilité. Les consommateurs downstream lisent indifféremment dict ou objet.
            new_seg = {
                "id": _seg_field(seg, "id"),
                "start": start,
                "end": end,
                "text": " " + marker + " ",
                "avg_logprob": _seg_field(seg, "avg_logprob"),
                "compression_ratio": _seg_field(seg, "compression_ratio"),
                "no_speech_prob": _seg_field(seg, "no_speech_prob"),
                "temperature": _seg_field(seg, "temperature"),
                "_cleaner_marked_inaudible": True,
                "_cleaner_reason": reason,
                "_cleaner_original_text": (text or "")[:200],
            }
            cleaned.append(new_seg)

        inaudible_dur += dur
        inaudible_ranges.append({
            "start_sec": round(start, 2),
            "end_sec": round(end, 2),
            "duration_sec": round(dur, 2),
            "reason": reason,
            "original_text_preview": (text or "").strip()[:120],
        })
        if reason_metrics is not None:
            n_by_metrics += 1
        else:
            n_by_text += 1

    if n_by_metrics + n_by_text > 0:
        logger.info(
            "Segment cleaner: %d/%d segments marqués inaudibles (par métriques: %d, par texte: %d). "
            "Durée gardée: %.1fs / %.1fs (%.0f%%).",
            n_by_metrics + n_by_text, len(ordered), n_by_metrics, n_by_text,
            usable, total, (100 * usable / total) if total > 0 else 0,
        )

    return {
        "cleaned_segments": cleaned,
        "inaudible_ranges": inaudible_ranges,
        "usable_duration_sec": round(usable, 2),
        "inaudible_duration_sec": round(inaudible_dur, 2),
        "total_duration_sec": round(total, 2),
        "usable_ratio": (usable / total) if total > 0 else 1.0,
        "stats": {
            "n_segments": len(ordered),
            "n_marked_by_metrics": n_by_metrics,
            "n_marked_by_text": n_by_text,
            "n_kept": n_kept,
        },
    }


def _total_duration(segments: list[Any]) -> float:
    if not segments:
        return 0.0
    return sum(max(0.0, _seg_end(s) - _seg_start(s)) for s in segments)


def format_inaudible_summary_for_telegram(
    summary: dict[str, Any], *, ui_locale: str = "fr"
) -> str:
    """Formatte ``inaudible_summary`` (produit par ``clean_segments``) en message Markdown Telegram.

    Aligné sur la décision de blocage (/pdf) : on montre la durée exploitable et la liste des
    passages inaudibles. Pas de "score Whisper 56%" qui sème la confusion.
    """
    is_ar = (ui_locale or "fr").lower().startswith("ar")

    usable_ratio = float(summary.get("usable_ratio") or 0)
    usable_pct = usable_ratio * 100
    inaudible_sec = float(summary.get("inaudible_duration_sec") or 0)
    usable_sec = float(summary.get("usable_duration_sec") or 0)
    total_sec = float(summary.get("total_duration_sec") or 0)
    ranges = summary.get("inaudible_ranges") or []

    # Icône en fonction du ratio utilisable
    if usable_pct >= 95:
        icon = "✅"
    elif usable_pct >= 80:
        icon = "🟢"
    elif usable_pct >= 60:
        icon = "🟡"
    else:
        icon = "🔴"

    if is_ar:
        head = (
            f"{icon} *تقرير الجودة*\n\n"
            f"• المدة القابلة للاستغلال: *{usable_pct:.0f}%* "
            f"({_fmt_dur(usable_sec, is_ar)} من {_fmt_dur(total_sec, is_ar)})\n"
            f"• مقاطع غير مسموعة: *{len(ranges)}* "
            f"({_fmt_dur(inaudible_sec, is_ar)})\n"
        )
    else:
        head = (
            f"{icon} *Rapport de qualité*\n\n"
            f"• Durée exploitable : *{usable_pct:.0f}%* "
            f"({_fmt_dur(usable_sec, is_ar)} sur {_fmt_dur(total_sec, is_ar)})\n"
            f"• Passages inaudibles : *{len(ranges)}* "
            f"({_fmt_dur(inaudible_sec, is_ar)})\n"
        )

    if not ranges:
        head += "\n👍 " + (
            "اكتمل النسخ بدون مشاكل." if is_ar
            else "Aucun passage inaudible — transcription complète et fiable."
        )
        return head

    head += "\n" + (
        "*المقاطع غير المسموعة :*\n" if is_ar
        else "*Détail des passages inaudibles :*\n"
    )

    # Affiche jusqu'à 10 plages (sinon le message déborde 4096 chars Telegram)
    for r in ranges[:10]:
        start = _fmt_ts(r["start_sec"])
        end = _fmt_ts(r["end_sec"])
        preview = (r.get("original_text_preview") or "").strip()
        if preview and len(preview) > 70:
            preview = preview[:67].rstrip() + "…"
        if preview:
            head += f"\n`[{start}-{end}]` _{preview}_"
        else:
            head += f"\n`[{start}-{end}]`"

    if len(ranges) > 10:
        if is_ar:
            head += f"\n\n_…و{len(ranges) - 10} مقاطع أخرى._"
        else:
            head += f"\n\n_…et {len(ranges) - 10} autres passages._"

    if not is_ar:
        head += (
            "\n\n_Le contenu original de ces zones n'est pas fiable (Whisper a halluciné) — il a été "
            "remplacé par des marqueurs ``[inaudible MM:SS]`` dans le transcript. La fiche `/pdf` les "
            "saute automatiquement._"
        )

    return head


def _fmt_ts(seconds: float) -> str:
    """Secondes → MM:SS ou HH:MM:SS."""
    try:
        s = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "00:00"
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:02d}:{ss:02d}"


def _fmt_dur(seconds: float, is_ar: bool = False) -> str:
    try:
        s = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "0s"
    if s < 60:
        return f"{s}s" if not is_ar else f"{s} ثا"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}" if not is_ar else f"{s // 60} د{s % 60:02d}"
    return f"{s // 3600}h{(s % 3600) // 60:02d}" if not is_ar else f"{s // 3600} س{(s % 3600) // 60:02d}"
