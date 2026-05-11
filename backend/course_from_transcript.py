"""Pipeline cours : JSON passages ASR annotés (Whisper) → collage Groq → cours Groq (map-reduce si besoin)."""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any, Optional

from groq import APIError, Groq

from groq_errors import is_groq_rate_limit_error

logger = logging.getLogger(__name__)

COLLAGE_SYSTEM = """You receive a JSON document (user message) named INPUT_JSON with:
- `min_trusted_score`: a numeric threshold on the 0–100 scale.
- `passages`: an array of objects. Each object has at least:
  - `text`: speech-to-text for that span
  - `reliability`: Whisper-derived quality data, including `score_0_100` and `high_reliability`, plus raw fields such as `avg_logprob`, `no_speech_prob`, `compression_ratio` when available.

Your tasks:
1. Treat passages with `reliability.score_0_100 >= min_trusted_score` as the **authoritative** source for meaning and facts.
2. You may use passages below the threshold only as light glue between trusted parts, or omit them if they look like noise or hallucinated filler.
3. Merge what you keep into **one** continuous lecture-style prose, in order of `start_sec` when present, otherwise `passage_index`.
4. Fix spelling, punctuation, and obvious ASR mis-hearings **without** inventing content or adding topics not supported by the trusted material.
5. Preserve the language(s) of the passages (no translation unless the source clearly mixes languages on purpose).
6. Output **only** the cleaned integrated prose — paragraphs separated by blank lines. No JSON, no commentary about scores, no lesson headings."""


MAP_NOTES_SYSTEM = """You extract study-relevant factual content from a lecture excerpt for later course authoring.
Output concise bullet notes in the same language as the excerpt. Preserve terminology and names. Do not invent content."""


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _groq_client(api_key: str) -> Groq:
    """Client Groq ; ``GROQ_BASE_URL`` optionnel (proxy OpenAI-compatible auto-hébergé)."""
    base = (os.getenv("GROQ_BASE_URL") or "").strip().rstrip("/")
    if base:
        return Groq(api_key=api_key, base_url=base)
    return Groq(api_key=api_key)


def _max_passage_reliability_score(annotation_doc: dict[str, Any]) -> float:
    m = 0.0
    for p in annotation_doc.get("passages") or []:
        if not isinstance(p, dict):
            continue
        r = p.get("reliability")
        if not isinstance(r, dict):
            continue
        try:
            m = max(m, float(r.get("score_0_100") or 0))
        except (TypeError, ValueError):
            pass
    return m


def _passages_from_mixed_view(transcript_mixed_view: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    blocks = transcript_mixed_view.get("blocks")
    if not isinstance(blocks, list):
        return out
    ix = 0
    for b in blocks:
        if not isinstance(b, dict) or b.get("kind") != "text":
            continue
        wr = b.get("whisper_reliability")
        if not isinstance(wr, dict):
            continue
        tx = str(b.get("display") or b.get("original") or "").strip()
        if not tx:
            continue
        out.append(
            {
                "passage_index": ix,
                "start_sec": None,
                "end_sec": None,
                "source_file": None,
                "text": tx,
                "reliability": wr,
            }
        )
        ix += 1
    return out


def build_asr_annotation_input(
    *,
    asr_passages_annotated: Optional[list[Any]],
    transcript_mixed_view: Optional[dict[str, Any]],
    transcript_plain: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Construit le document JSON (passages + fiabilité) pour Groq.
    Lève ValueError('missing_asr_annotations') si aucune source exploitable (y compris repli transcript).
    """
    meta: dict[str, Any] = {"annotation_source": None, "passage_count": 0}
    passages: list[dict[str, Any]] = []

    if asr_passages_annotated and isinstance(asr_passages_annotated, list):
        for p in asr_passages_annotated:
            if not isinstance(p, dict):
                continue
            tx = str(p.get("text") or "").strip()
            if not tx:
                continue
            rel = p.get("reliability")
            if not isinstance(rel, dict):
                continue
            passages.append(
                {
                    "passage_index": int(p.get("passage_index", len(passages))),
                    "start_sec": p.get("start_sec"),
                    "end_sec": p.get("end_sec"),
                    "source_file": p.get("source_file"),
                    "text": tx,
                    "reliability": rel,
                }
            )
        if passages:
            meta["annotation_source"] = "asr_passages_annotated"
            meta["passage_count"] = len(passages)

    if not passages and transcript_mixed_view and isinstance(transcript_mixed_view, dict):
        passages = _passages_from_mixed_view(transcript_mixed_view)
        if passages:
            meta["annotation_source"] = "transcript_mixed_view"
            meta["passage_count"] = len(passages)

    if not passages:
        tp = (transcript_plain or "").strip()
        if len(tp) >= 50:
            passages = [
                {
                    "passage_index": 0,
                    "start_sec": None,
                    "end_sec": None,
                    "source_file": None,
                    "text": tp,
                    "reliability": {
                        "high_reliability": True,
                        "score_0_100": 95.0,
                        "avg_logprob": None,
                        "no_speech_prob": None,
                        "compression_ratio": None,
                        "temperature": None,
                        "thresholds": {},
                        "note": "transcript_plain_fallback",
                    },
                }
            ]
            meta["annotation_source"] = "transcript_plain_fallback"
            meta["passage_count"] = 1

    if not passages:
        raise ValueError("missing_asr_annotations")

    doc = {
        "schema_version": 1,
        "kind": "whisper_passage_annotations",
        "passages": passages,
    }
    return doc, meta


def _json_payload_under_max_chars(payload_obj: dict[str, Any], json_max: int) -> tuple[str, dict[str, Any]]:
    """
    Sérialise ``payload_obj`` en JSON valide dont la longueur ≤ ``json_max``.
    Retire d’abord des passages de fin, puis raccourcit le texte du dernier passage (jamais de troncature
    brutale au milieu d’une chaîne JSON — cela cassait Groq).
    """
    trunc_meta: dict[str, Any] = {}
    obj = json.loads(json.dumps(payload_obj, ensure_ascii=False))
    for _ in range(2000):
        dumped = json.dumps(obj, ensure_ascii=False)
        if len(dumped) <= json_max:
            return dumped, trunc_meta
        passages = obj.get("passages")
        if not isinstance(passages, list):
            passages = []
            obj["passages"] = passages
        if len(passages) > 1:
            passages.pop()
            trunc_meta["strategy"] = "dropped_trailing_passages"
            continue
        if len(passages) == 1 and isinstance(passages[0], dict):
            tx = str(passages[0].get("text") or "")
            if len(tx) > 200:
                cut = max(120, len(tx) - max(400, (len(dumped) - json_max) * 2))
                passages[0]["text"] = tx[:cut] + "\n[…]"
                trunc_meta["strategy"] = "shortened_passage_text"
                continue
            if len(tx) > 50:
                passages[0]["text"] = tx[: max(50, len(tx) // 2)] + "…"
                trunc_meta["strategy"] = "hard_short"
                continue
        break
    dumped = json.dumps(obj, ensure_ascii=False)
    if len(dumped) <= json_max:
        return dumped, trunc_meta
    ms = obj.get("min_trusted_score")
    try:
        ms_f = float(ms) if ms is not None else 90.0
    except (TypeError, ValueError):
        ms_f = 90.0
    ps = obj.get("passages") if isinstance(obj.get("passages"), list) else []
    raw_t = str(ps[0].get("text")) if ps and isinstance(ps[0], dict) else ""
    cap = max(400, min(8000, json_max // 5))
    emergency: dict[str, Any] = {
        "min_trusted_score": ms_f,
        "schema_version": 1,
        "kind": "whisper_passage_annotations",
        "passages": [
            {
                "passage_index": 0,
                "text": raw_t[:cap],
                "reliability": {"high_reliability": True, "score_0_100": 90.0, "note": "emergency_min_json_payload"},
            }
        ],
    }
    trunc_meta["strategy"] = "emergency_min_json_payload"
    return json.dumps(emergency, ensure_ascii=False), trunc_meta


def _split_text_chunks(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paras = text.split("\n\n")
    chunks: list[str] = []
    buf: list[str] = []
    cur_len = 0
    for p in paras:
        sep = 2 if buf else 0
        add_len = len(p) + sep
        if buf and cur_len + add_len > max_chars:
            chunks.append("\n\n".join(buf))
            buf = [p]
            cur_len = len(p)
        else:
            buf.append(p)
            cur_len += add_len
    if buf:
        chunks.append("\n\n".join(buf))
    out: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            out.append(c)
            continue
        for i in range(0, len(c), max_chars):
            out.append(c[i : i + max_chars])
    return out


def _groq_chat(
    client: Groq,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    extra_create_kwargs: Optional[dict[str, Any]] = None,
) -> tuple[str, int, int]:
    """
    Appel chat completions avec retries exponentiels sur rate limit (429 / TPM / RPM).
    Config : ``GROQ_HTTP_MAX_RETRIES`` (défaut 5), ``GROQ_HTTP_RETRY_BASE_SEC`` (défaut 2.5).
    """
    max_retries = _env_int("GROQ_HTTP_MAX_RETRIES", 5)
    max_retries = max(1, min(max_retries, 12))
    base_sec = _env_float("GROQ_HTTP_RETRY_BASE_SEC", 2.5)
    base_sec = max(0.5, min(base_sec, 30.0))

    create_kw: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if extra_create_kwargs:
        create_kw.update(extra_create_kwargs)

    last_exc: Optional[APIError] = None
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(**create_kw)
            choice0 = (completion.choices or [None])[0]
            text = (getattr(choice0.message, "content", None) or "").strip()
            usage = getattr(completion, "usage", None)
            inp = int(getattr(usage, "prompt_tokens", None) or 0) if usage else 0
            out = int(getattr(usage, "completion_tokens", None) or 0) if usage else 0
            return text, inp, out
        except APIError as e:
            last_exc = e
            if not is_groq_rate_limit_error(e) or attempt >= max_retries - 1:
                raise
            sleep_s = min(90.0, base_sec * (2**attempt) + random.uniform(0, 0.6))
            logger.warning(
                "Groq rate limit, tentative %s/%s — nouvel essai dans %.1fs (%s)",
                attempt + 1,
                max_retries,
                sleep_s,
                model,
            )
            time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


def run_course_pipeline(
    *,
    api_key: str,
    subject: str,
    transcript: str,
    asr_passages_annotated: Optional[list[Any]],
    transcript_mixed_view: Optional[dict[str, Any]],
    lesson_system_prompt: str,
    model: str,
    max_tokens_lesson: int,
) -> tuple[str, int, int, dict[str, Any]]:
    """
    Retourne (lesson_markdown, prompt_tokens_sum, completion_tokens_sum, pipeline_meta).
    """
    min_score = _env_float("GENERATE_MIN_WHISPER_RELIABILITY_SCORE", 90.0)
    min_score = max(0.0, min(100.0, min_score))

    annotation_doc, ann_meta = build_asr_annotation_input(
        asr_passages_annotated=asr_passages_annotated,
        transcript_mixed_view=transcript_mixed_view,
        transcript_plain=transcript,
    )

    collage_model = (os.getenv("GROQ_COLLAGE_MODEL") or "").strip() or model
    map_model = (os.getenv("GROQ_MAP_NOTES_MODEL") or "").strip() or model

    collage_max = _env_int("GROQ_COLLAGE_MAX_TOKENS", 16384)
    collage_max = max(512, min(collage_max, 32768))
    map_max = _env_int("GROQ_MAP_MAX_TOKENS", 4096)
    map_max = max(256, min(map_max, 8192))

    map_chunk_chars = _env_int("GENERATE_MAP_CHUNK_CHARS", 12000)
    map_chunk_chars = max(2000, min(map_chunk_chars, 100_000))
    single_max_source = _env_int("GENERATE_SINGLE_LESSON_MAX_SOURCE_CHARS", 45000)
    single_max_source = max(8000, min(single_max_source, 200_000))
    reduce_cap = _env_int("GENERATE_REDUCE_MAX_NOTES_CHARS", 95000)
    reduce_cap = max(10_000, min(reduce_cap, 500_000))

    json_max = _env_int("GENERATE_ASR_JSON_MAX_CHARS", 280_000)
    json_max = max(20_000, min(json_max, 500_000))

    passages_list = list(annotation_doc.get("passages") or [])
    max_pass_score = _max_passage_reliability_score(annotation_doc)
    # Whisper local : segments souvent sans avg_logprob → score 0 ; le collage « min_trusted » n’a alors
    # aucune source « fiable » et peut échouer ou renvoyer du vide — on saute directement au transcript.
    force_skip_col = _env_truthy("GENERATE_SKIP_COLLAGE", False)
    skip_col = force_skip_col or (bool(passages_list) and max_pass_score < min_score)

    client = _groq_client(api_key)
    usage_in = 0
    usage_out = 0
    json_payload = ""
    json_trunc_meta: dict[str, Any] = {}
    skip_reason: Optional[str] = None

    if skip_col:
        skip_reason = "forced_env" if force_skip_col else "no_passage_meets_min_trusted_score"
        integrated = (transcript or "").strip()
        cin = cout = 0
    else:
        payload_obj = {
            "min_trusted_score": min_score,
            **annotation_doc,
        }
        json_payload, json_trunc_meta = _json_payload_under_max_chars(payload_obj, json_max)
        collage_user = f"""Subject / theme hint: {subject}

INPUT_JSON:
{json_payload}"""
        try:
            integrated, cin, cout = _groq_chat(
                client,
                model=collage_model,
                system=COLLAGE_SYSTEM,
                user=collage_user,
                max_tokens=collage_max,
                temperature=0.2,
            )
        except APIError:
            raise
        usage_in += cin
        usage_out += cout

    fallback_plain = (transcript or "").strip()
    if not integrated.strip():
        integrated = fallback_plain

    pipeline: dict[str, Any] = {
        **ann_meta,
        "min_trusted_score": min_score,
        "max_passage_reliability_score": max_pass_score,
        "collage_skipped": skip_col,
        "collage_skip_reason": skip_reason,
        "collage_json_chars": len(json_payload) if json_payload else 0,
        "collage_input_json_truncation": (json_trunc_meta or None) if not skip_col else None,
        "collage_chars_out": len(integrated),
        "map_reduce": False,
        "map_chunks": 0,
    }

    source_for_lesson = integrated.strip()
    if len(source_for_lesson) > single_max_source:
        pipeline["map_reduce"] = True
        chunks = _split_text_chunks(source_for_lesson, map_chunk_chars)
        pipeline["map_chunks"] = len(chunks)
        note_parts: list[str] = []
        for i, ch in enumerate(chunks):
            u = f"""This is excerpt part {i + 1} of {len(chunks)} of the same lecture (after ASR cleanup). Extract factual study notes (bullets only, same language).

--- 
{ch}
---"""
            notes, ni, no = _groq_chat(
                client,
                model=map_model,
                system=MAP_NOTES_SYSTEM,
                user=u,
                max_tokens=map_max,
                temperature=0.15,
            )
            usage_in += ni
            usage_out += no
            if notes.strip():
                note_parts.append(f"### Part {i + 1}\n{notes.strip()}")
        consolidated = "\n\n".join(note_parts).strip()
        if len(consolidated) > reduce_cap:
            consolidated = consolidated[:reduce_cap] + "\n\n[… notes tronquées pour limite modèle …]"
        source_for_lesson = consolidated

    lesson_user = f"""Subject: {subject}

SOURCE (cleaned lecture text or consolidated study notes derived from the annotated ASR pipeline):
{source_for_lesson}

Please generate a complete, structured lesson from this material."""

    try:
        lesson, lin, lout = _groq_chat(
            client,
            model=model,
            system=lesson_system_prompt,
            user=lesson_user,
            max_tokens=max_tokens_lesson,
            temperature=0.35,
        )
    except APIError:
        raise
    usage_in += lin
    usage_out += lout

    if not lesson.strip():
        raise ValueError("empty_lesson")

    return lesson.strip(), usage_in, usage_out, pipeline
