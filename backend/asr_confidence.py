"""Agrégation des scores de confiance Whisper par passage et globalement.

Whisper expose plusieurs métriques par segment (no_speech_prob, avg_logprob, compression_ratio,
temperature). ``transcribe.py:_whisper_reliability_payload`` les combine déjà en un score 0-100
par segment et le stocke dans ``asr_passages_annotated[i].reliability.score_0_100``.

Ce module en tire :
  - Un **score global** pondéré par la durée de chaque passage (un long passage à 60 % pèse
    plus qu'un court passage à 60 %).
  - La liste des **passages sous le seuil** (par défaut 90 %), avec timestamp et texte.
  - Des **statistiques** : % de durée à faible confiance, médiane, etc.

Utilisé par :
  - ``transcribe.py`` pour ajouter ``result_json.confidence_summary`` au job
  - ``telegram/processor.py`` (commande ``/confiance``) pour montrer les passages problématiques
  - (Optionnel) ``asr_retry.py`` pour cibler les segments à re-transcrire

Sans dépendance externe.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _passage_score(passage: dict[str, Any]) -> Optional[float]:
    """Extrait le score 0-100 d'un passage, ou ``None`` si absent / invalide."""
    rel = passage.get("reliability") if isinstance(passage, dict) else None
    if not isinstance(rel, dict):
        return None
    s = rel.get("score_0_100")
    try:
        score = float(s)
    except (TypeError, ValueError):
        return None
    if score < 0.0 or score > 100.0:
        return None
    return score


def _passage_duration(passage: dict[str, Any]) -> float:
    """Durée en secondes du passage. 0 si invalide ou trop court."""
    try:
        start = float(passage.get("start_sec") or 0)
        end = float(passage.get("end_sec") or 0)
        d = end - start
        return max(0.0, d)
    except (TypeError, ValueError):
        return 0.0


def compute_overall(
    passages: list[dict[str, Any]],
    *,
    low_conf_threshold: float = 90.0,
    max_low_conf_returned: int = 30,
) -> dict[str, Any]:
    """Agrège les scores des passages en un résumé.

    Args:
        passages: liste de dicts comme stockée dans ``result_json.asr_passages_annotated``.
        low_conf_threshold: seuil sous lequel un passage est considéré « low confidence ».
        max_low_conf_returned: limite la liste retournée (sinon un transcript de 1h peut renvoyer 1000 entrées).

    Returns:
        Dict avec :
          - ``overall_score_0_100``: score moyen **pondéré par la durée** (None si pas de données)
          - ``simple_avg_0_100``: moyenne arithmétique non pondérée (utile pour comparer)
          - ``n_passages``, ``n_passages_with_score``
          - ``n_below_threshold``, ``duration_below_sec``, ``ratio_below``: stats sur les zones faibles
          - ``low_conf_passages``: liste tronquée à ``max_low_conf_returned``, triée par score croissant
          - ``threshold_used``: rappel du seuil
    """
    if not passages:
        return _empty_summary(low_conf_threshold)

    scored: list[tuple[dict[str, Any], float, float]] = []  # (passage, score, duration)
    n_passages = 0
    for p in passages:
        if not isinstance(p, dict):
            continue
        n_passages += 1
        score = _passage_score(p)
        dur = _passage_duration(p)
        if score is None:
            continue
        scored.append((p, score, dur))

    if not scored:
        return {**_empty_summary(low_conf_threshold), "n_passages": n_passages}

    # Score moyen pondéré durée : un long passage compte plus qu'un court.
    # Si toutes les durées sont 0 (cas pathologique), fallback sur moyenne simple.
    total_dur = sum(d for _, _, d in scored)
    if total_dur > 0:
        weighted = sum(score * d for _, score, d in scored) / total_dur
    else:
        weighted = sum(score for _, score, _ in scored) / len(scored)

    simple = sum(score for _, score, _ in scored) / len(scored)

    low = [(p, score, dur) for p, score, dur in scored if score < low_conf_threshold]
    duration_below = sum(d for _, _, d in low)
    ratio_below = (duration_below / total_dur) if total_dur > 0 else 0.0

    # Tri par score croissant (pire d'abord) — l'user veut voir les passages les plus problématiques.
    low_sorted = sorted(low, key=lambda x: x[1])

    low_conf_passages_out: list[dict[str, Any]] = []
    for p, score, dur in low_sorted[:max_low_conf_returned]:
        text = (p.get("text") or "").strip()
        # On tronque le texte pour ne pas exploser la taille du JSON / des messages Telegram
        if len(text) > 280:
            text = text[:277].rstrip() + "…"
        low_conf_passages_out.append({
            "passage_index": p.get("passage_index"),
            "start_sec": round(float(p.get("start_sec") or 0), 2),
            "end_sec": round(float(p.get("end_sec") or 0), 2),
            "duration_sec": round(dur, 2),
            "score_0_100": round(score, 1),
            "text_preview": text,
        })

    return {
        "overall_score_0_100": round(weighted, 1),
        "simple_avg_0_100": round(simple, 1),
        "n_passages": n_passages,
        "n_passages_with_score": len(scored),
        "n_below_threshold": len(low),
        "duration_below_sec": round(duration_below, 2),
        "total_duration_sec": round(total_dur, 2),
        "ratio_below": round(ratio_below, 3),
        "low_conf_passages": low_conf_passages_out,
        "threshold_used": low_conf_threshold,
        "truncated": len(low) > max_low_conf_returned,
    }


def _empty_summary(threshold: float) -> dict[str, Any]:
    return {
        "overall_score_0_100": None,
        "simple_avg_0_100": None,
        "n_passages": 0,
        "n_passages_with_score": 0,
        "n_below_threshold": 0,
        "duration_below_sec": 0.0,
        "total_duration_sec": 0.0,
        "ratio_below": 0.0,
        "low_conf_passages": [],
        "threshold_used": threshold,
        "truncated": False,
    }


def format_summary_for_telegram(summary: dict[str, Any], *, ui_locale: str = "fr") -> str:
    """Formatte le résumé en texte Markdown lisible pour Telegram.

    Affiche le score global, le nombre de passages problématiques, et liste les pires (timestamps + extrait).
    """
    is_ar = (ui_locale or "fr").lower().startswith("ar")

    overall = summary.get("overall_score_0_100")
    if overall is None:
        return (
            "ℹ️ لا توجد بيانات ثقة متاحة لهذا الدرس."
            if is_ar
            else "ℹ️ Pas de données de confiance disponibles pour ce cours."
        )

    n_below = summary.get("n_below_threshold", 0)
    total_below_sec = summary.get("duration_below_sec", 0.0)
    threshold = summary.get("threshold_used", 90.0)
    ratio_below = (summary.get("ratio_below") or 0.0) * 100
    low_passages = summary.get("low_conf_passages") or []

    # Icône en fonction du score global
    if overall >= 90:
        icon = "✅"
    elif overall >= 75:
        icon = "🟡"
    elif overall >= 60:
        icon = "🟠"
    else:
        icon = "🔴"

    if is_ar:
        head = (
            f"{icon} *تقرير الثقة*\n\n"
            f"• الثقة الإجمالية: *{overall:.1f}%*\n"
            f"• مقاطع تحت {threshold:.0f}%: *{n_below}* "
            f"({_fmt_duration(total_below_sec, is_ar)}, {ratio_below:.0f}% من المدة)\n"
        )
    else:
        head = (
            f"{icon} *Rapport de confiance*\n\n"
            f"• Confiance globale : *{overall:.1f}%*\n"
            f"• Passages < {threshold:.0f}% : *{n_below}* "
            f"({_fmt_duration(total_below_sec, is_ar)}, {ratio_below:.0f}% de la durée)\n"
        )

    if not low_passages:
        head += (
            "\n👍 لا توجد مقاطع منخفضة الثقة." if is_ar
            else "\n👍 Aucun passage problématique."
        )
        return head

    head += (
        "\n*أسوأ المقاطع :*\n" if is_ar
        else "\n*Passages les plus problématiques :*\n"
    )

    # Max 8 passages affichés dans le message Telegram (sinon ça déborde les 4096 chars).
    for p in low_passages[:8]:
        start = _fmt_timestamp(p["start_sec"])
        end = _fmt_timestamp(p["end_sec"])
        score = p["score_0_100"]
        preview = p["text_preview"]
        head += f"\n`[{start}-{end}]` *{score:.0f}%* — {preview}"

    if len(low_passages) > 8:
        head += (
            f"\n\n_…et {len(low_passages) - 8} autres passages problématiques._"
            if not is_ar
            else f"\n\n_…و{len(low_passages) - 8} مقاطع أخرى._"
        )

    if summary.get("truncated"):
        head += (
            "\n_(Liste tronquée — d'autres passages problématiques existent.)_"
            if not is_ar
            else "\n_(القائمة مختصرة.)_"
        )

    return head


def _fmt_timestamp(seconds: float) -> str:
    """Secondes → ``MM:SS`` (ou ``HH:MM:SS`` si > 1h)."""
    try:
        s = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "00:00"
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:02d}:{ss:02d}"


def _fmt_duration(seconds: float, is_ar: bool = False) -> str:
    try:
        s = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "0s"
    if s < 60:
        return f"{s}s" if not is_ar else f"{s} ثا"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}" if not is_ar else f"{s // 60} د{s % 60:02d}"
    return f"{s // 3600}h{(s % 3600) // 60:02d}" if not is_ar else f"{s // 3600} س{(s % 3600) // 60:02d}"
