"""Post-traitement LLM du transcript ASR — corrige les erreurs évidentes sans réécrire le sens.

But : nettoyer les erreurs Whisper typiques (homophones « j'essuie » / « je suis », syllabes
sautées « atomie » / « anatomie », ponctuation absente) **après** la transcription, sans risque
de boucle d'hallucination comme avec ``initial_prompt``.

Pourquoi c'est plus sûr que le prompt Whisper :
  - Le LLM voit du **texte court** par chunks (~3000 chars), pas un long audio
  - Instructions strictes : ne pas reformuler, ne pas compléter les phrases tronquées
  - Détection post-hoc de boucles / hallucinations → on rejette l'output suspect

Coût : ~1 appel Groq par tranche de 3000 chars. Pour un cours de 1h (~7000 mots ≈ 45000 chars),
~15 appels Groq → quelques centaines de tokens par tranche → ~0.05 MRU total. Négligeable.

Désactivable via ``TRANSCRIBE_ASR_CLEANUP_ENABLED=false``.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

# Taille cible d'une tranche. On vise une longueur où le LLM garde le contexte tout en restant rapide.
# 3000 chars ≈ 500 mots ≈ ~750 tokens entrée + ~750 sortie = appel rapide (~1-2s sur Groq).
_CHUNK_TARGET_CHARS = 3000

# Rapport max output/input — si le LLM produit beaucoup plus que l'entrée, c'est une hallucination /
# expansion suspecte. On rejette et on garde l'original. Seuil large (1.3) pour tolérer la ponctuation
# ajoutée légitimement.
_MAX_RESPONSE_RATIO = 1.3
# Rapport min — si le LLM coupe plus de la moitié, il a probablement compris « résume ».
_MIN_RESPONSE_RATIO = 0.5

# Détection de boucle : un seul mot représentant > 15 % du contenu = boucle pathologique.
_LOOP_TOP_WORD_RATIO = 0.15
_LOOP_MIN_WORDS = 20


def is_enabled() -> bool:
    return (os.getenv("TRANSCRIBE_ASR_CLEANUP_ENABLED", "true").strip().lower()
            in ("1", "true", "yes", "on"))


def cleanup_transcript(text: str, *, language: str = "fr", subject: Optional[str] = None) -> str:
    """Nettoie un transcript ASR via Groq. Retourne le texte nettoyé, ou l'original si échec/risque.

    Garanties :
      - Jamais d'exception : toute erreur réseau / LLM / parsing → retour de l'original
      - Pas de doublement / hallucination : chaque chunk dont l'output est suspect est rejeté
      - Aucun changement si ``TRANSCRIBE_ASR_CLEANUP_ENABLED=false`` ou texte trop court
    """
    if not is_enabled():
        return text
    if not text or len(text) < 200:
        # Trop court pour valoir le coup — et plus court = plus de risque que le LLM s'invente du contexte.
        return text

    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not api_key:
        logger.warning("ASR cleanup: GROQ_API_KEY absent — skip")
        return text

    try:
        from groq import Groq
    except ImportError:
        logger.warning("ASR cleanup: package `groq` introuvable — skip")
        return text

    chunks = _split_into_chunks(text, _CHUNK_TARGET_CHARS)
    if len(chunks) == 1 and len(chunks[0]) > _CHUNK_TARGET_CHARS * 2:
        # Impossible de couper proprement (pas de phrases ?) → on saute, plus sûr.
        logger.info("ASR cleanup: 1 seul chunk trop long (%d chars) — skip", len(chunks[0]))
        return text

    client = Groq(api_key=api_key)
    model = (os.getenv("GROQ_GENERATE_MODEL") or "llama-3.3-70b-versatile").strip()

    cleaned_chunks: list[str] = []
    fail_count = 0
    for i, chunk in enumerate(chunks):
        try:
            candidate = _cleanup_chunk(client, model, chunk, language=language, subject=subject)
        except Exception:
            logger.warning("ASR cleanup chunk %d/%d : appel Groq a planté — garde original",
                           i + 1, len(chunks), exc_info=True)
            cleaned_chunks.append(chunk)
            fail_count += 1
            continue

        if _is_safe_cleanup(candidate, chunk):
            cleaned_chunks.append(candidate)
        else:
            logger.warning("ASR cleanup chunk %d/%d : output suspect (boucle / size) — garde original",
                           i + 1, len(chunks))
            cleaned_chunks.append(chunk)
            fail_count += 1

    if fail_count >= len(chunks):
        # Tout a foiré → c'est peut-être un problème de modèle. Pas de bénéfice net.
        logger.warning("ASR cleanup: tous les chunks ont échoué — retour du texte original")
        return text

    return "\n\n".join(cleaned_chunks)


def _split_into_chunks(text: str, target_chars: int) -> list[str]:
    """Découpe sur frontières de phrases (regex). Préfère couper net même si chunk un peu sous/sur la cible."""
    text = text.strip()
    if len(text) <= int(target_chars * 1.3):
        return [text]

    # Lookbehind : on coupe après .!? suivi d'un espace
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for s in sentences:
        s_len = len(s)
        if current and current_len + s_len + 1 > target_chars:
            chunks.append(" ".join(current).strip())
            current = [s]
            current_len = s_len
        else:
            current.append(s)
            current_len += s_len + 1
    if current:
        chunks.append(" ".join(current).strip())
    return [c for c in chunks if c]


def _build_system_prompt(language: str, subject: Optional[str]) -> str:
    """Construit le prompt système — *strict* pour éviter toute reformulation."""
    if language.startswith("ar"):
        return (
            "أنت مدقق نصوص نسخ صوتي عربي. مهمتك الوحيدة: تصحيح الأخطاء الواضحة فقط "
            "(تشابه نطق، حروف ناقصة). لا تعد صياغة المعنى أبدًا.\n"
            "قواعد صارمة:\n"
            "1. حافظ على نفس عدد الجمل تقريبًا.\n"
            "2. لا تكمل أي جملة منقوصة — اتركها كما هي.\n"
            "3. إذا شككت في التصحيح، احتفظ بالأصل.\n"
            "4. أعد فقط النص المصحح، بدون مقدمات ولا شرح."
        )
    ctx = (
        f"Contexte : transcript d'un cours sur « {subject} ». Tu peux t'attendre à du vocabulaire "
        "spécialisé de ce domaine.\n"
        if subject and subject.lower() not in ("general", "général", "auto")
        else ""
    )
    return (
        "Tu es un correcteur de transcript ASR français. Ta SEULE mission : corriger les erreurs "
        "lexicales évidentes (homophones type 'j'essuie' ↔ 'je suis', mots tronqués type "
        "'atomie' → 'anatomie', ponctuation manifestement absente entre 2 phrases). "
        "Tu NE reformules JAMAIS le sens.\n"
        "Règles strictes :\n"
        "1. Garde EXACTEMENT le même nombre de phrases.\n"
        "2. Ne complète JAMAIS une phrase tronquée ou inachevée — laisse-la telle quelle.\n"
        "3. Ne supprime aucun mot ; ne paraphrase pas ; ne rajoute pas d'idées.\n"
        "4. Si tu hésites sur une correction, garde l'ORIGINAL.\n"
        "5. Ne change pas le registre, le ton, les répétitions de l'orateur.\n"
        f"{ctx}"
        "Renvoie UNIQUEMENT le texte corrigé, sans préambule, sans guillemets, sans markdown."
    )


def _cleanup_chunk(client, model: str, chunk: str, *, language: str, subject: Optional[str]) -> str:
    """Un appel Groq pour un chunk."""
    system = _build_system_prompt(language, subject)
    user = f"Texte ASR à corriger :\n\n{chunk}"
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # Température 0 : déterministe, pas de créativité (créativité = hallucination ici).
        temperature=0.0,
        # On limite la sortie pour réduire encore le risque d'expansion catastrophique.
        max_tokens=min(2048, int(len(chunk) / 2)),
    )
    return (resp.choices[0].message.content or "").strip()


def _is_safe_cleanup(cleaned: str, original: str) -> bool:
    """Heuristiques anti-catastrophe.

    Détecte :
      - Output beaucoup plus court → LLM a résumé / sauté du contenu
      - Output beaucoup plus long → LLM a inventé du contexte
      - Boucle pathologique (1 mot > 15 % du total) → comme Whisper avec prompt
    """
    if not cleaned:
        return False
    ratio = len(cleaned) / max(1, len(original))
    if ratio < _MIN_RESPONSE_RATIO or ratio > _MAX_RESPONSE_RATIO:
        return False

    words = cleaned.split()
    if len(words) >= _LOOP_MIN_WORDS:
        # On normalise un peu : minuscules, sans ponctuation collée, pour matcher les boucles.
        norm = [re.sub(r"[^\w'-]", "", w.lower()) for w in words]
        norm = [w for w in norm if len(w) >= 3]  # ignore "de", "la", "à"
        if norm:
            top_word, top_count = Counter(norm).most_common(1)[0]
            if top_count / len(norm) > _LOOP_TOP_WORD_RATIO:
                logger.debug("ASR cleanup: boucle détectée '%s' (%d/%d mots)",
                             top_word, top_count, len(norm))
                return False
    return True
