"""Inférence sujet + résumé profond sur un transcript, via l’API Groq (LLM)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GROQ_MODEL_DEFAULT = "llama-3.1-8b-instant"
_MAX_INPUT_CHARS = 110_000


def _truncate_transcript(text: str) -> tuple[str, bool]:
    t = (text or "").strip()
    if len(t) <= _MAX_INPUT_CHARS:
        return t, False
    head = int(_MAX_INPUT_CHARS * 0.78)
    tail = _MAX_INPUT_CHARS - head - 80
    return f"{t[:head]}\n\n[...]\n\n{t[-tail:]}", True


def _parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    s = (raw or "").strip()
    if not s:
        return None
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    try:
        out = json.loads(s)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _lang_line(speech_language: str) -> str:
    sl = (speech_language or "fr").strip().lower()[:2]
    if sl == "ar":
        return "Write every user-facing string in Modern Standard Arabic when the transcript is Arabic; if the transcript is clearly another language, match that language instead."
    return "Write every user-facing string in French when the transcript is French; if the transcript is clearly another language, match that language instead."


def groq_infer_subject_and_deep_summary(
    *,
    transcript_plain: str,
    user_subject_hint: str,
    speech_language: str,
) -> Optional[dict[str, Any]]:
    """
    Retourne un dict avec inferred_subject, deep_summary, prompt_tokens, completion_tokens, transcript_truncated,
    ou None si désactivé / erreur.
    """
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        return None
    plain = (transcript_plain or "").strip()
    if len(plain) < 80:
        return None

    try:
        from groq import APIError, Groq

        from course_from_transcript import _groq_chat
    except ImportError:
        logger.warning("groq_transcript_intel: package 'groq' manquant — pip install groq")
        return None

    model = (os.getenv("GROQ_INSIGHT_MODEL") or "").strip() or _GROQ_MODEL_DEFAULT
    body, truncated = _truncate_transcript(plain)
    hint = (user_subject_hint or "").strip()
    hint_line = f'User-provided subject hint (may be wrong or generic): "{hint}"\n' if hint else ""

    system = f"""You are an expert academic analyst. You read raw speech-to-text lecture transcripts (noisy, oral style).

Return ONLY valid JSON with exactly these keys:
- "inferred_subject": string, a precise short title for the TRUE main topic (discipline + focus), not the file name. Ignore the user hint if it contradicts the transcript.
- "deep_summary": string in Markdown. A dense, high-depth synthesis (several substantial sections) including: central question/thesis; key arguments and concepts in order; methods or theories if any; implications or applications; limits, tensions, or open questions. No fluff. Use ## headings and bullet lists where useful.

{_lang_line(speech_language)}
Do not invent facts not supported by the transcript; you may note uncertainty explicitly."""

    user = f"""{hint_line}TRANSCRIPT:
{body}"""

    try:
        base = (os.getenv("GROQ_BASE_URL") or "").strip().rstrip("/")
        client = Groq(api_key=key, base_url=base) if base else Groq(api_key=key)
        raw_text, pt, ct = _groq_chat(
            client,
            model=model,
            system=system,
            user=user,
            max_tokens=8192,
            temperature=0.25,
            extra_create_kwargs={"response_format": {"type": "json_object"}},
        )
    except APIError:
        logger.warning("groq_transcript_intel: erreur API (remontée au routeur)", exc_info=True)
        raise
    except Exception:
        logger.warning("groq_transcript_intel: appel moteur échoué", exc_info=True)
        return None

    parsed = _parse_json_object(raw_text)
    if not parsed:
        logger.warning("groq_transcript_intel: JSON Groq illisible")
        return None

    subj = str(parsed.get("inferred_subject") or "").strip()
    summ = str(parsed.get("deep_summary") or "").strip()
    if not subj and not summ:
        return None

    return {
        "inferred_subject": subj,
        "deep_summary": summ,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "transcript_truncated": truncated,
    }


def groq_clean_transcript_segments(
    segments: list[dict[str, Any]],
    speech_language: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Applique le "Prompt Maître" pour corriger le texte des segments (erreurs phonétiques, dialectes)
    tout en conservant la structure JSON (passage_index) pour maintenir les scores de fiabilité.
    """
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key or not segments:
        return segments, 0, 0

    try:
        from groq import APIError, Groq
        from course_from_transcript import _groq_chat
    except ImportError:
        return segments, 0, 0

    model = (os.getenv("GROQ_INSIGHT_MODEL") or "").strip() or _GROQ_MODEL_DEFAULT
    base = (os.getenv("GROQ_BASE_URL") or "").strip().rstrip("/")
    client = Groq(api_key=key, base_url=base) if base else Groq(api_key=key)

    lang_display = "français" if not speech_language or speech_language.lower() == "fr" else speech_language
    
    system = f"""Tu es un expert en intelligence artificielle, spécialisé en linguistique (Français, Arabe, dialecte Hassaniya) et en correction post-transcription. Ton objectif est de transformer une transcription brute contenant des erreurs en un texte fluide, parfait et 100% compréhensible, sans ajouter de balises de locuteurs.

Instructions strictes :
1. Traduction Arabe & Hassaniya : Les locuteurs utilisent de l'arabe ou du dialecte Hassaniya. Tu dois traduire ces passages fluidement en {lang_display}.
2. Correction Contextuelle : Utilise le contexte pour corriger les erreurs phonétiques de la machine.
3. Son bas / Inaudible : Remplace les passages totalement incohérents par [Inaudible].
4. Zéro Balise de Locuteur : N'ajoute JAMAIS "Intervenant :" ou "Speaker :".
5. Rythme et Ponctuation : Ajoute ponctuation et majuscules naturelles.
6. FORMAT DE SORTIE OBLIGATOIRE : Tu dois retourner EXACTEMENT le même tableau JSON sous la clé "segments", en modifiant uniquement la valeur de "text". Ne supprime ni n'ajoute aucun segment. L'attribut 'id' doit rester identique.

Renvoyer UNIQUEMENT le JSON sous la forme: {{"segments": [{{"id": 0, "text": "..."}}, ...]}}"""

    batch_size = 40
    total_pt = 0
    total_ct = 0
    
    out_segments = [dict(s) for s in segments]
    
    for i in range(0, len(segments), batch_size):
        batch = segments[i:i+batch_size]
        mini_batch = [{"id": s.get("passage_index", j), "text": s.get("text", "")} for j, s in enumerate(batch)]
        user = "Voici le tableau JSON des segments à corriger :\n" + json.dumps(mini_batch, ensure_ascii=False)
        
        try:
            raw_text, pt, ct = _groq_chat(
                client,
                model=model,
                system=system,
                user=user,
                max_tokens=8192,
                temperature=0.1,
                extra_create_kwargs={"response_format": {"type": "json_object"}}
            )
            total_pt += pt
            total_ct += ct
            
            parsed = _parse_json_object(raw_text)
            if parsed and "segments" in parsed and isinstance(parsed["segments"], list):
                corrected_dict = {str(item.get("id", "")): item.get("text", "") for item in parsed["segments"] if isinstance(item, dict)}
                
                for s_in in batch:
                    pid = str(s_in.get("passage_index", ""))
                    if pid in corrected_dict:
                        for o_s in out_segments:
                            if str(o_s.get("passage_index", "")) == pid:
                                o_s["text"] = corrected_dict[pid]
                                break
        except Exception as e:
            logger.warning(f"groq_clean_transcript_segments: erreur sur le batch {{i}}: {{e}}")
            
    return out_segments, total_pt, total_ct
