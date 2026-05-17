"""Orchestrateur principal du bot WhatsApp — exécuté en background task depuis le webhook.

Flow d'un message audio :
  1. Trouver le user par ``whatsapp_phone`` (sinon → message d'inscription, return).
  2. Vérifier le portefeuille (sinon → message recharge, return).
  3. Anti-spam : rate-limit 1 audio / 30s par numéro.
  4. ACK rapide ("audio reçu, je travaille").
  5. Télécharger le média Meta vers ``data/jobs/<public_id>/upload.<ext>``.
  6. Créer un ``TranscriptionJob`` (``source='whatsapp'``, ``whatsapp_phone``, ``whatsapp_message_id``).
  7. Spawn ``execute_transcription_job`` (réutilise la pipeline existante).
  8. Poll en boucle jusqu'à ``status='done'`` (ou ``failed`` / timeout).
  9. Appeler ``run_course_pipeline`` pour générer le cours markdown.
  10. Construire le PDF avec ``simple_pdf.build_lesson_pdf_bytes``.
  11. Upload le PDF vers Meta (``client.upload_document``) puis ``send_document``.

Robustesse :
  - Toute exception inattendue → un message d'erreur user-friendly + log stack trace.
  - Timeout global pour éviter qu'un job pourri bloque le worker indéfiniment.
  - Idempotence ``whatsapp_message_id`` : on dédoublonne en base avant de créer un job.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from credits_wallet import wallet_block_reason
from database import SessionLocal
from models import TranscriptionJob, User
from pricing import wallet_units_to_mru_display
from . import client as wa_client
from . import config as wa_config
from .messages import lang_for, t
from .parser import InboundMessage
from .simple_pdf import build_lesson_pdf_bytes

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"
_RATE_LIMIT_SECONDS = 30.0
# Dispatcher (webhook → réponse rapide) : doit rester sous la limite Meta (5s pour ACK, mais le ACK
# est déjà envoyé en background). 15 min suffit largement pour download + DB insert + kick-off.
_PROCESS_TIMEOUT_SECONDS = float(os.getenv("WHATSAPP_PROCESS_TIMEOUT_SEC", "900"))
# Livraison PDF : tâche détachée — couvre toute la durée de la transcription (Whisper local
# sur 2h d'audio peut prendre 2h+ sur CPU). Défaut 2h, configurable.
_DELIVERY_TIMEOUT_SECONDS = float(os.getenv("WHATSAPP_DELIVERY_TIMEOUT_SEC", "7200"))
_TRANSCRIBE_POLL_INTERVAL = 2.5

# Modèle de transcription par défaut sur WhatsApp si l'user n'a pas encore choisi.
# whisper-large-v3-turbo : bon compromis prix (5 MRU/h) / qualité / vitesse.
_WA_DEFAULT_MODEL = "whisper-large-v3-turbo"

# Alias user-facing → id canonique du catalogue (transcription_retail_catalog.RETAIL_MODELS).
# Le moteur "eco" (local Whisper CPU) a été retiré : trop lent et marge nulle.
_WA_MODEL_ALIASES: dict[str, str] = {
    "turbo": "whisper-large-v3-turbo",
    "equilibre": "whisper-large-v3",
    "équilibre": "whisper-large-v3",
    "affine": "gpt-4o-mini-transcribe",
    "affiné": "gpt-4o-mini-transcribe",
    "excellence": "whisper-1",
}

# Rate-limit in-memory simple ({phone: last_audio_epoch}). Tolérant car notre VPS = 1 worker uvicorn.
# Pour multi-worker plus tard : passer à Redis.
_last_audio_at: dict[str, float] = {}

# Sessions de quiz interactif en mémoire — {phone: {questions, idx, score, ui_loc, job_public_id}}.
# Volatile : un redémarrage pm2 efface, l'user peut relancer via /quiz. Acceptable.
_quiz_sessions: dict[str, dict[str, Any]] = {}

# Astuces déjà montrées à un user : {phone: {hint_key, ...}}. Évite de radoter la même astuce.
# Volatile (in-memory) — un restart pm2 réinitialise et l'user reverra les astuces : acceptable, mieux
# vaut quelques rappels en trop qu'un user paumé.
_hints_seen: dict[str, set[str]] = {}

# Audios téléchargés en attente de choix de matière par l'user — {phone: {public_id, rel_path, ...}}.
# Volatile : un restart pm2 abandonne les audios pending (fichier orphelin sur disque, cleanup manuel).
_pending_audio: dict[str, dict[str, Any]] = {}
_PENDING_AUDIO_TTL_SECONDS = float(os.getenv("WHATSAPP_PENDING_AUDIO_TTL_SEC", "600"))  # 10 min
# Auto-skip : si l'user ne répond pas à la question de matière en N secondes, on traite
# l'audio comme s'il avait tapé /skip (détection auto). Défaut 10s, surchargeable.
_PENDING_AUDIO_AUTO_SKIP_SECONDS = float(os.getenv("WHATSAPP_AUTO_SKIP_MATIERE_SEC", "10"))


async def _maybe_hint(phone: str, key: str, ui_loc: str, **fmt: Any) -> None:
    """Envoie une astuce contextuelle, au plus une fois par session pour un même ``key``."""
    seen = _hints_seen.setdefault(phone, set())
    if key in seen:
        return
    seen.add(key)
    msg = t(key, ui_loc, **fmt)
    if msg and msg != key:
        await _safe_send(phone, msg)


async def _auto_skip_pending_audio_after(phone: str, public_id: str, delay_seconds: float) -> None:
    """Tâche détachée : après ``delay_seconds``, si l'audio est toujours en attente de matière,
    on déclenche automatiquement le flow ``/skip`` (détection auto de la matière).

    On vérifie ``public_id`` pour éviter de déclencher si l'user a entre-temps envoyé un *nouvel*
    audio (le pending a alors été remplacé pour ce phone → on ne touche pas le nouveau).
    """
    try:
        await asyncio.sleep(max(1.0, delay_seconds))
    except asyncio.CancelledError:
        return
    pending = _pending_audio.get(phone)
    if pending is None or pending.get("public_id") != public_id:
        # User a déjà répondu OU un nouvel audio a remplacé le pending → on ne fait rien
        return
    logger.info("Auto-skip matière déclenché pour %s (public_id=%s) après %.0fs", phone, public_id, delay_seconds)
    _cleanup_pending_audio(phone, delete_file=False)
    ui_loc = pending.get("ui_loc") or wa_config.default_language()
    await _safe_send(phone, t("matiere_skipped_auto", ui_loc))
    await _run_audio_pipeline_for_user(
        phone=phone,
        ui_loc=ui_loc,
        user_id=pending["user_id"],
        public_id=pending["public_id"],
        rel_path=pending["rel_path"],
        mime=pending.get("mime"),
        message_id=pending["message_id"],
        initial_subject="General",
        explicit_subject=False,
    )


def _cleanup_pending_audio(phone: str, *, delete_file: bool) -> Optional[dict[str, Any]]:
    """Supprime l'entrée pending pour ``phone``. Si ``delete_file``, retire aussi le fichier staged.

    Retourne l'entrée supprimée (pour permettre d'extraire les métadonnées) ou None.
    """
    pending = _pending_audio.pop(phone, None)
    if pending and delete_file:
        rel = pending.get("rel_path")
        if rel:
            try:
                abs_path = _DATA / rel
                if abs_path.exists():
                    abs_path.unlink()
                parent = abs_path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception:
                logger.debug("Cleanup pending audio file failed", exc_info=True)
    return pending

# Mots-clés (FR + AR) → libellé de matière. Premier match gagne.
_SUBJECT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("mathématique", "mathematique", "algèbre", "algebre", "géométrie", "geometrie",
      "dérivée", "derivee", "intégrale", "integrale", "équation", "equation",
      "رياضيات", "جبر", "هندسة", "معادلة"), "Mathématiques"),
    (("physique", "newton", "mécanique", "mecanique", "électricité", "electricite",
      "thermodynamique", "optique", "فيزياء"), "Physique"),
    (("chimie", "molécule", "molecule", "réaction chimique", "reaction chimique",
      "كيمياء"), "Chimie"),
    (("biologie", "cellule", "adn", "génétique", "genetique", "biologique",
      "أحياء", "بيولوجيا"), "SVT"),
    (("histoire", "guerre", "empire", "révolution", "revolution", "تاريخ"), "Histoire"),
    (("géographie", "geographie", "climat", "continent", "جغرافيا"), "Géographie"),
    (("philosophie", "philosophique", "métaphysique", "metaphysique", "فلسفة"), "Philosophie"),
    (("informatique", "programmation", "algorithme", "ordinateur", "code source",
      "حاسوب", "برمجة"), "Informatique"),
    (("économie", "economie", "marché", "marche", "inflation", "اقتصاد"), "Économie"),
    (("éducation islamique", "education islamique", "coran", "hadith", "fiqh",
      "تربية إسلامية", "قرآن", "حديث", "فقه"), "Éducation islamique"),
    (("langue arabe", "grammaire arabe", "نحو", "صرف", "بلاغة", "لغة عربية"), "Langue arabe"),
    (("français", "francais", "littérature", "litterature", "conjugaison"), "Français"),
]


def _build_plan_prompt_fr() -> str:
    """Pass 1 : prompt minimal pour produire un plan détaillé en markdown."""
    return """Tu es un expert pédagogue. À partir du transcript ci-dessous, produis un PLAN DÉTAILLÉ pour une fiche de cours complète.

INSTRUCTIONS :
- 4 à 8 grandes sections (## Titre)
- Pour chaque section : 2-4 sous-sections (### Titre) si le contenu le justifie
- Sous chaque section / sous-section : liste 3-6 *points-clés à développer* (un par bullet, avec mention des exemples concrets à inclure)
- À la fin : liste les 8-15 termes-clés du cours à inclure dans le glossaire
- NE DÉVELOPPE RIEN — juste l'ossature détaillée

Format markdown strict. Pas de paragraphes développés ici, c'est juste le squelette."""


def _build_plan_json_prompt_fr() -> str:
    """Pass 1 alternative : retourne le plan en JSON structuré pour itération machine."""
    return """Tu es un expert pédagogue. À partir du transcript fourni, produis le PLAN structuré du *corps* d'une fiche de cours.

Retourne UN OBJET JSON EXACTEMENT au format suivant, sans aucun texte avant ou après :
{
  "sections": [
    {
      "title": "titre concret de la section 1 (pas 'Introduction' ni 'Conclusion')",
      "points": ["point clé 1 à développer", "point clé 2 avec exemple X", "..."]
    },
    ...
  ],
  "glossary_terms": ["terme1", "terme2", "..."]
}

Règles :
- 4 à 8 sections *de contenu* couvrant les notions enseignées
- *INTERDIT* : section nommée "Introduction", "Conclusion", "Synthèse", "Résumé", "Glossaire", "Cheat sheet", "Quiz", "Flashcards", "Pour aller plus loin". Ces sections sont générées séparément en annexes. Le plan ne contient que les concepts/chapitres du cours.
- Pour chaque section : 4 à 8 points-clés à développer (assez de matière pour un paragraphe nourri par point)
- glossary_terms : 8 à 15 termes-clés mentionnés
- N'invente RIEN d'absent du transcript
- Réponds UNIQUEMENT par le JSON (pas de markdown, pas de texte de présentation)."""


def _build_plan_json_prompt_ar() -> str:
    return """أنت خبير تربوي. من التسجيل المقدّم، أنشئ خطّة منظّمة لبطاقة درس.

أعد كائن JSON بالضبط بالتنسيق التالي، دون أي نص قبل أو بعد:
{
  "sections": [
    {
      "title": "عنوان القسم 1",
      "points": ["نقطة مفتاحية 1 لتطويرها", "نقطة 2 مع مثال", "..."]
    },
    ...
  ],
  "glossary_terms": ["مصطلح1", "مصطلح2", "..."]
}

القواعد:
- 4 إلى 8 أقسام
- لكل قسم: 4 إلى 8 نقاط مفتاحية
- glossary_terms: 8 إلى 15 مصطلحًا أساسيًا
- لا تخترع شيئًا غير موجود في التسجيل
- أجب بـ JSON فقط (لا ماركداون، لا نص تقديمي)."""


def _build_plan_prompt_ar() -> str:
    return """أنت خبير تربوي. من التسجيل أدناه، أنشئ خطّة *مفصّلة* لبطاقة درس كاملة.

التعليمات:
- 4 إلى 8 أقسام رئيسية (## عنوان)
- لكل قسم: 2-4 أقسام فرعية (### عنوان) عند اللزوم
- تحت كل قسم/قسم فرعي: قائمة من 3-6 *نقاط رئيسية* (نقطة لكل سطر، مع ذكر الأمثلة الملموسة المطلوبة)
- في النهاية: 8-15 مصطلحًا أساسيًا للمسرد
- لا تطوّر شيئًا — هذا مجرد هيكل

تنسيق ماركداون. لا فقرات متطوّرة هنا، فقط هيكل."""


def _build_section_dev_prompt_fr(section_title: str, points: list[str], target_words: int) -> str:
    """Prompt pour développer UNE section — mentalité étudiant, dense, avec garde-fous anti-tautologie."""
    points_md = "\n".join(f"- {p}" for p in points)
    return f"""Tu es un *pair étudiant brillant* qui développe UNE section de fiche de cours pour un autre étudiant. Adopte la mentalité étudiant :

🧠 Boussole : comprendre > résumer · donne le pourquoi · anticipe les pièges réels · mémorisation active · hiérarchise (🔥/⭐/💡) · auto-éval qui TESTE LA COMPRÉHENSION · ton de pair · adapte à la matière.

📌 SECTION : *{section_title}*
📋 Points à couvrir :
{points_md}
🎯 Longueur : ≥ {target_words} mots (impératif).

✏️ Format OBLIGATOIRE :
- Commence par `## {section_title} 🔥` OU `## {section_title} ⭐` OU `## {section_title} 💡`
  (chaque section DOIT porter un de ces 3 tags : 🔥 = critique, ⭐ = important, 💡 = bonus)
- Sous-sections `###`, **3-5 paragraphes développés** par sous-section (= **plusieurs phrases reliées**, jamais 1 ligne)
- *Au moins UN exemple concret CHIFFRÉ* par point-clé (pas juste "par exemple en médecine" — donne un cas, des nombres, une situation)
- *Au moins UNE analogie* par section ("imagine que c'est comme...")
- *Au moins UN encadré* par section : 💡 *Astuce :* … OU ⚠️ *Erreur fréquente :* … OU 📌 *À retenir :* …
- *Au moins UN "🧪 Test rapide :* …" par section, qui force un calcul ou une distinction (pas une définition récitée)
- **Gras** uniquement sur les vrais termes-clés

🚫 ANTI-FILLER (lecture obligatoire) :
- ❌ Pas de sous-section `### Définition de {section_title}` qui ne fait que recopier la 1ʳᵉ phrase. La définition appartient à l'ouverture du paragraphe, pas à une sous-section creuse.
- ❌ Pas de méta-narration ("dans cette section, nous allons voir…", "nous allons résumer / discuter / aborder…"). Va directement au contenu.
- ❌ Pas de conclusion-sommaire en fin de section ("en conclusion, nous avons vu…", "en résumé, X est..."). Si la section est "Conclusion", elle doit contenir une *vraie synthèse* (3-5 idées-clés concrètes + 1 message-clé).
- ❌ Pas de bullets squelettiques. Si tu fais une liste, chaque item doit faire ≥ 1 phrase explicative.

🛡️ TESTS RAPIDES INLINE — règle stricte :
Un "🧪 *Test rapide :*" DOIT être une **vraie question** qui force l'étudiant à *réfléchir*, *calculer* ou *décider*. Tu donnes l'énoncé, point. **Tu N'ÉCRIS PAS la réponse dans la même boîte.**
- ❌ NUL : *"Test rapide : Quelle est la différence entre X et Y ? X est ... tandis que Y est ..."* (la réponse est directement écrite dans le test → c'est juste un Q&A déguisé, zéro test).
- ❌ NUL : *"Test rapide : Comment déterminer si... ? Vous pouvez utiliser le test du chi²."* (réponse direct).
- ✅ BON : *"Test rapide : Calcule la moyenne et la variance de la série : 12, 14, 18, 20, 21. Puis vérifie : la moyenne est-elle robuste si on remplace 21 par 100 ?"*
- ✅ BON : *"Test rapide : Tu obtiens r = -0.85 entre la durée d'étude et le taux d'erreur. Comment l'interprètes-tu ? Une causalité est-elle prouvée ?"*

→ Si tu veux donner une correction, tu peux ajouter en italique sous le test : *"(Réponse en fin de fiche)"*. Mais **jamais** la réponse en clair dans le test rapide lui-même.

🛡️ ANTI-TAUTOLOGIES (à lire 2 fois) :

❌ MAUVAIS "Test rapide" :
   *Qu'est-ce que la moyenne ?*
   - A) La valeur la plus fréquente
   - B) La valeur moyenne ✅
   - C) La valeur médiane
   - D) La variance
   ⤷ NUL : la réponse est dans la question. Aucune réflexion. ZÉRO info.

✅ BON "Test rapide" :
   *Sur la série 2, 4, 4, 10, 20, la moyenne est-elle plus représentative que la médiane ?*
   - A) Oui, car elle utilise toutes les valeurs
   - B) Non, car la valeur 20 (outlier) la tire vers le haut ✅
   - C) Oui, car médiane = mode dans ce cas
   - D) Non, car la médiane est toujours plus précise
   *Explication :* la moyenne (8) est tirée par 20, la médiane (4) reflète mieux le centre.

❌ MAUVAISE "Erreur fréquente" :
   *Confondre moyenne et médiane. La moyenne est la valeur moyenne, la médiane est la valeur médiane.*
   ⤷ NUL : tautologie pure. N'explique rien.

✅ BONNE "Erreur fréquente" :
   *Penser que la moyenne et la médiane donnent la même chose. La moyenne = somme/n (sensible aux outliers) ; la médiane = valeur centrale après tri (robuste aux outliers). Sur (1, 2, 3, 4, 100), moyenne = 22, médiane = 3.*

Règle générale : si tu peux remplacer la phrase par "X est X", c'est nul. Une *Erreur fréquente* doit nommer la confusion *et* donner le critère qui les distingue *avec un exemple chiffré ou concret*. Un *Test rapide* doit forcer une réflexion (calcul, déduction, distinction de cas), jamais un simple appariement mot↔mot.

❌ Interdit : bullets squelettiques, paraphrase paresseuse, faux exemples, ton "prof à élève", tautologies.
🚫 Ne génère QUE cette section (pas d'intro globale, pas de conclusion, pas de quiz)."""


def _build_section_dev_prompt_ar(section_title: str, points: list[str], target_words: int) -> str:
    points_md = "\n".join(f"- {p}" for p in points)
    return f"""أنت *طالب متميّز* تكتب قسمًا من بطاقة درس لطالب آخر.

🧠 البوصلة: الفهم > التلخيص · أعطِ لماذا · حذّر من أفخاخ حقيقية · حفظ نشط · أولوية (🔥/⭐/💡) · تقييم يختبر الفهم · نبرة قرين · اضبط حسب المادة.

📌 القسم: *{section_title}*
📋 النقاط:
{points_md}
🎯 الطول: ≥ {target_words} كلمة.

✏️ التنسيق:
- `## {section_title}` (مع علامة 🔥/⭐/💡 عند اللزوم)
- `###` للأقسام الفرعية، 3-5 فقرات متطوّرة
- مثال ملموس + تشبيه
- خانات: 💡 *نصيحة* / ⚠️ *خطأ شائع* / 📌 *لتذكّر*
- 1-2 "🧪 *اختبار سريع:* …" بعد المفاهيم الكبرى
- **عريض** للمصطلحات فقط

🛡️ منع الحشو التكراري:

❌ سيئ: *ما هو المتوسط؟ B) القيمة المتوسطة ✅* — السؤال يحتوي الجواب، صفر فائدة.
✅ جيد: *في السلسلة 2, 4, 4, 10, 20، هل المتوسط أكثر تمثيلًا من الوسيط؟* A/B/C/D مع شرح حقيقي.

❌ سيئ: *الخلط بين المتوسط والوسيط. المتوسط هو القيمة المتوسطة والوسيط هو القيمة الوسيطية.* — تكرار أجوف.
✅ جيد: *الاعتقاد أن المتوسط = الوسيط. المتوسط = المجموع/n (حسّاس للقيم الشاذة)، الوسيط = القيمة المركزية بعد الترتيب. على (1, 2, 3, 4, 100): المتوسط = 22، الوسيط = 3.*

القاعدة: إذا أمكن استبدال الجملة بـ "X هو X" فهي سيئة. اختبار سريع يجب أن يفرض تفكيرًا (حساب، استنتاج)، لا مجرد مطابقة.

❌ ممنوع: قوائم هيكلية، أمثلة وهمية، إيجاز مفرط، تكرار تعريفي.
🚫 هذا القسم فقط."""


def _build_annexes_prompt_fr(glossary_terms: list[str], expl_label: str) -> str:
    terms = ", ".join(glossary_terms[:30]) if glossary_terms else ""
    return f"""Tu es un *pair étudiant brillant* — tu rédiges les annexes d'une fiche (le corps du cours est déjà écrit). Ton de pair, engageant, concret. ZÉRO académisme creux, ZÉRO méta-narration.

Produis ces sections *dans cet ordre exact* (le programme réordonnera l'intro avant le corps et le reste après) :

## Introduction (8-12 lignes rédigées)
Contexte concret du cours, pourquoi *c'est utile pour l'étudiant* (exemples : "tu en auras besoin pour…", "à l'examen on demande typiquement…"), à qui ça s'adresse. PAS de méta ("dans ce cours nous verrons…") — entre dans le vif tout de suite.

## Glossaire — 8-15 termes
À inclure (au moins) : {terms}
Format : **Terme** — définition 1-2 phrases *avec un mini exemple chiffré ou concret*. Pas de définition abstraite "X est l'étude de Y".

## Tableau récapitulatif (≥ 6 lignes)
| Concept | À retenir (en 1 ligne) |
Notions essentielles, plus critiques en premier.

## Cheat sheet 1-page (≤ 300 mots, ULTRA-DENSE)
Format VEILLE D'EXAMEN. Inclus OBLIGATOIREMENT :
- 📐 *Formules clés* — chacune avec UNE interprétation 1-ligne ("ça dit que…")
- 🎯 *Seuils typiques* — valeurs de référence à connaître par cœur (ex. "r > 0.7 = fort, 0.3-0.7 = moyen, < 0.3 = faible")
- 🗺️ *Quel outil pour quel cas* — mini table de décision si applicable
- 🧠 *Mnémo* — moyens de retenir (acronymes, phrases-clés, analogies)
- 🚨 *Pièges récurrents en examen* — 3 max, concrets

❌ Pas de simple "liste de mots-clés". Dense en info.

## Quiz (5 QCM, BREF)
A) B) C) D), ✅ pour la bonne, '{expl_label}:' en 1 phrase.

⚠️ RÈGLE CRITIQUE pour CHAQUE question :
- ❌ INTERDIT : "Qu'est-ce que X ? → A) la définition de X". Les mots-clés de la bonne réponse NE DOIVENT PAS se retrouver dans la question.
- ❌ INTERDIT : "D) Toutes les réponses sont correctes" (paresse de QCM).
- ✅ ATTENDU : calcul, distinction de cas, application, choix entre concepts proches, identification d'erreurs.

## Flashcards (5, BREF)
Format Q: … / A: … — testent la *compréhension* (application, cause, différence chiffrée), pas la récitation de définition.

## Conclusion (8-15 lignes, ZÉRO méta)
⚠️ RÈGLE CRITIQUE :
- ❌ INTERDIT : "Dans cette conclusion, nous allons résumer / discuter / aborder…" → NUL.
- ✅ ATTENDU :
  1. Les *3-5 idées-clés à retenir absolument* (concrètes, pas génériques)
  2. *Un message final* (pourquoi ce cours change ta façon de voir le sujet ; comment tu l'utiliseras concrètement)
  3. *Une mise en garde finale* (le piège à éviter en examen, ou le lien avec le prochain cours)

## Pour aller plus loin (3 sujets)
3 pistes connexes pour approfondir.

❌ N'invente rien d'absent du transcript. Concis sur quiz/flashcards/aller-plus-loin. INTRO, CHEAT SHEET, CONCLUSION doivent être *vraiment travaillées*."""


def _build_annexes_prompt_ar(glossary_terms: list[str], expl_label: str) -> str:
    terms = "، ".join(glossary_terms[:30]) if glossary_terms else ""
    return f"""أنت *طالب متميّز* — تكتب ملحقات بطاقة درس (الجسم مكتوب). نبرة قرين، عملي، ملموس. ممنوع الإطالة الأكاديمية الفارغة.

أنتج هذه الأقسام بالترتيب:

## مقدمة (8-12 سطر)
السياق الملموس + لماذا هذا مفيد للطالب + الجمهور. ادخل في الموضوع مباشرة. ممنوع "في هذا الدرس سنرى…".

## مسرد — 8-15 مصطلح
ضمّن: {terms}
الصيغة: **مصطلح** — تعريف من 1-2 جملة *مع مثال موجز ملموس أو رقمي*.

## جدول ملخّص (≥ 6 صفوف)
| المفهوم | للتذكّر |
الأولوية للأهم.

## ورقة تلخيص في صفحة (≤ 300 كلمة، كثيفة)
صيغة ليلة الامتحان:
- 📐 الصيغ الأساسية مع شرح سطر واحد
- 🎯 العتبات النموذجية (مثل "r > 0.7 = قوي")
- 🗺️ أي أداة لأي حالة
- 🧠 وسائل تذكر / تشبيهات
- 🚨 الأفخاخ المتكررة في الامتحان (3 كحد أقصى)

## اختبار (5 أسئلة، مختصر)
A) B) C) D)، ✅، '{expl_label}:' جملة.
⚠️ ممنوع: السؤال يحتوي إجابته. ممنوع "D) كل الإجابات صحيحة".
✅ المطلوب: حساب، تمييز، تطبيق، تحديد خطأ.

## بطاقات (5، مختصر)
Q: … / A: … — تختبر الفهم لا الحفظ.

## خاتمة (8-15 سطر، صفر سرد فوقي)
⚠️ ممنوع: "في هذه الخاتمة سنلخّص ونناقش ونعرض…"
✅ المطلوب: 3-5 أفكار مفتاحية + رسالة ختامية + تحذير من فخّ في الامتحان.

## للاستزادة (3 مواضيع)

❌ لا تخترع شيئًا. اعمل بجدّ على المقدمة، ورقة التلخيص، والخاتمة."""


def _build_lesson_prompt_fr(target_words_body: int) -> str:
    """Prompt fiche WhatsApp (single-pass / fallback) — mentalité étudiant, dense."""
    return f"""Tu es un *pair étudiant brillant* qui rédige la fiche que tu aurais voulu avoir. Ton public : un étudiant qui n'a pas pu suivre le cours, ou qui révise pour l'examen.

🚨 RÈGLE ABSOLUE — passages inaudibles :
Le transcript peut contenir des marqueurs ``[inaudible MM:SS-MM:SS]`` (ou en arabe : ``[غير مسموع …]``) là où l'enregistrement était dégradé. Tu DOIS :
- *Ne JAMAIS inventer* de contenu pour combler ces zones. Pas de fausse cohérence, pas de devine.
- *Sauter* ces plages dans le contenu principal — n'écris pas de section dessus.
- *Mentionner* en fin d'introduction si > 10 % du cours est inaudible : "⚠️ Note : certaines parties (X min) du cours étaient inaudibles à l'enregistrement et ne sont pas couvertes dans cette fiche."
- *Ne pas marquer* dans la fiche elle-même les timestamps inaudibles (l'user les voit déjà ailleurs).

🧠 MENTALITÉ ÉTUDIANT (boussole pour TOUT) :
1. *Comprendre > résumer* — reformule les passages flous du prof, donne l'intuition avant la définition
2. *Le pourquoi* — pourquoi ce concept existe, quel problème il résout, où ça sert
3. *Anticipe les pièges* — "ne confonds pas X et Y", "erreur fréquente : …", "piège d'examen : …"
4. *Mémorisation active* — mnémotechniques, analogies visuelles ("imagine que…"), connexions surprenantes
5. *Hiérarchise* — 🔥 critique / ⭐ important / 💡 bonus dans les titres (pas tout égal)
6. *Auto-éval embarquée* — après chaque concept majeur : "Avant de continuer, peux-tu expliquer X ?"
7. *Lis l'intention du prof* — "Le prof a insisté ici → probablement à l'examen", "détaillé longuement…"
8. *Connexions* — prérequis ("tu dois connaître X"), liens ("similaire à Y vu avant")
9. *Ton de pair* — encourage aux endroits durs ("c'est normal si tu galères ici"), lucide, pas condescendant
10. *Adapte à la matière* — maths → dérivations step-by-step + formules encadrées ; histoire → chronologie + personnages ; sciences → hypothèse/expérience/résultat + ordres de grandeur ; droit → articles cités + cas pratiques ; langue → exemples de phrases en contexte

🎯 LONGUEUR (impératif) : corps (section 4) ≥ {target_words_body} mots. Interdit de compenser par quiz/flashcards (concis à la fin).

📋 STRUCTURE :
## 1. Titre — clair et engageant
## 2. Introduction (8-12 lignes rédigées) — contexte + enjeu + objectifs (pas de bullets)
## 3. Glossaire — 8-15 termes : **Terme** — définition 1-2 phrases + (si utile) bref exemple
## 4. Corps (≥ {target_words_body} mots) — 4-8 sections, sous-sections ### si pertinent. Pour chaque section :
   - Titre + tag d'importance (🔥/⭐/💡)
   - 3-5 paragraphes développés (pas de bullets squelettiques)
   - 1+ exemple concret par concept-clé + 1 analogie quand pertinent
   - Encadrés sélectifs : 💡 *Astuce/Takeaway* / ⚠️ *Erreur fréquente* / 📌 *À retenir*
   - "🧪 *Test rapide :* …" après les concepts majeurs (mini-question d'auto-éval)
## 5. Tableau récap — Concept | À retenir, ≥ 6 lignes
## 6. Quiz (5 QCM, BREF) — A) B) C) D), ✅, *Explication:* en 1 phrase
## 7. Flashcards (5, BREF) — Q: … / A: …
## 8. Cheat sheet 1-page — tout le cours en ultra-condensé pour la veille d'examen (encadrés, formules-clés, mots-clés ; ≤ 250 mots)
## 9. Pour aller plus loin — 3 sujets connexes

❌ INTERDIT : pavés d'intro creux, définitions sans exemple, paraphrase paresseuse, faux exemples ("par exemple…" sans rien), survol égalitaire, ton "prof à élève", inventer du contenu absent du transcript.

🛡️ ANTI-TAUTOLOGIES (impératif pour Tests rapides, Erreurs fréquentes, Quiz) :
- ❌ "Qu'est-ce que X ? → la valeur de X" (réponse dans la question, zéro info)
- ✅ Force une vraie réflexion : calcul, distinction de cas, choix entre concepts proches, application
- ❌ "Erreur fréquente : confondre X et Y. X est X et Y est Y."
- ✅ Nomme la confusion, donne le critère qui les distingue, et illustre avec un exemple chiffré ou concret

Test : si tu peux remplacer ta phrase par "X est X", c'est nul → réécris.

✏️ Markdown : ##, ###, **gras** *uniquement* sur les vrais termes-clés. Tableaux, listes selon besoin."""


def _build_lesson_prompt_ar(target_words_body: int) -> str:
    return f"""أنت *طالب متميّز* يكتب البطاقة التي كان يتمنّى الحصول عليها. الجمهور: طالب لم يحضر الدرس أو يراجع للامتحان.

🚨 قاعدة مطلقة — المقاطع غير المسموعة:
قد يحتوي النص على علامات ``[غير مسموع MM:SS-MM:SS]`` (أو بالفرنسية ``[inaudible …]``) في المواضع التي كان فيها التسجيل سيئًا. يجب عليك:
- *عدم اختلاق* أي محتوى لسد هذه الفجوات. لا تخمين، لا تكميل من خيالك.
- *تخطّي* هذه المقاطع في المحتوى الرئيسي — لا تكتب فقرات عنها.
- *الإشارة* في نهاية المقدمة إذا كان > 10% من الدرس غير مسموع: "⚠️ ملاحظة: بعض المقاطع (X د) لم تكن مسموعة في التسجيل ولم تُدرج في هذه البطاقة."
- *عدم وضع* علامات الوقت غير المسموعة داخل البطاقة (المستخدم يراها من مصدر آخر).

🧠 عقلية الطالب (بوصلة لكلّ ما تكتب):
1. *الفهم > التلخيص* — أعد صياغة العبارات الغامضة، قدّم الحدس قبل التعريف
2. *لماذا* — لماذا يوجد هذا المفهوم، أي مشكلة يحلّ، أين يُستخدم
3. *حذّر من الأخطاء* — "لا تخلط بين X و Y"، "خطأ شائع: …"، "فخّ امتحان: …"
4. *حفظ نشط* — وسائل تذكّر، تشبيهات بصرية ("تخيّل…")، روابط مفاجئة
5. *الأولوية* — 🔥 جوهري / ⭐ مهم / 💡 إثراء في العناوين
6. *تقييم ذاتي* — بعد كل مفهوم رئيسي: "قبل المتابعة، هل تستطيع شرح X؟"
7. *اقرأ نيّة الأستاذ* — "ركّز هنا → غالبًا في الامتحان"
8. *روابط* — متطلبات سابقة، تشابه مع مفاهيم درسناها
9. *نبرة قرين* — شجّع في النقاط الصعبة، صادق، غير متعالٍ
10. *اضبط حسب المادة* — رياضيات → اشتقاق خطوة بخطوة، تاريخ → خط زمني، علوم → فرضية/تجربة/نتيجة، حقوق → مواد + قضايا، لغات → جمل سياقية

🎯 الطول: جسم القسم 4 ≥ {target_words_body} كلمة. ممنوع التعويض بالاختبار/البطاقات.

📋 الهيكل:
## 1. العنوان
## 2. المقدمة (8-12 سطر نص)
## 3. مسرد — 8-15 مصطلح: **مصطلح** — تعريف + مثال موجز
## 4. الجسم (≥ {target_words_body} كلمة) — 4-8 أقسام (## وأقسام فرعية ###). لكل قسم:
   - عنوان + علامة أولوية (🔥/⭐/💡)
   - 3-5 فقرات متطوّرة
   - مثال ملموس واحد على الأقل + تشبيه عند اللزوم
   - خانات: 💡 *نصيحة* / ⚠️ *خطأ شائع* / 📌 *لتذكّر*
   - "🧪 *اختبار سريع:* …" بعد المفاهيم الكبرى
## 5. جدول ملخّص — المفهوم | للتذكّر، ≥ 6 صفوف
## 6. اختبار (5 أسئلة، مختصر) — A) B) C) D)، ✅، *تفسير:* جملة
## 7. بطاقات (5، مختصر) — Q: … / A: …
## 8. ورقة تلخيص في صفحة — كل الدرس مركّز لليلة الامتحان (≤ 250 كلمة)
## 9. للاستزادة — 3 مواضيع

❌ ممنوع: مقدمات فارغة، تعاريف بلا أمثلة، إعادة صياغة كسولة، أمثلة وهمية، معاملة كل شيء بالتساوي، نبرة فوقية، اختراع محتوى.

✏️ ماركداون: ##, ###, **عريض** للمصطلحات الأساسية فقط."""


def _split_annexes_intro_vs_rest(annexes_md: str) -> tuple[str, str]:
    """Sépare le bloc intro (à mettre AVANT le corps) du reste (après le corps).

    Cherche la 1ʳᵉ section H2 contenant 'introduction' / 'مقدمة' et la prend complète
    jusqu'à la prochaine H2. Le reste (glossaire, tableau, cheat sheet, etc.) → bloc final.
    """
    if not annexes_md or not annexes_md.strip():
        return "", ""
    lines = annexes_md.splitlines()
    intro_lines: list[str] = []
    rest_lines: list[str] = []
    state = "before"  # before, in_intro, after_intro
    for line in lines:
        if line.startswith("## ") or line.startswith("# "):
            head = line.lstrip("#").strip().lower()
            is_intro = "introduction" in head or "مقدمة" in head or "مقدّمة" in head
            if state == "before" and is_intro:
                state = "in_intro"
                intro_lines.append(line)
                continue
            if state == "in_intro":
                # On vient de tomber sur la H2 suivante → fin de l'intro
                state = "after_intro"
                rest_lines.append(line)
                continue
        if state == "in_intro":
            intro_lines.append(line)
        elif state == "after_intro":
            rest_lines.append(line)
        else:
            # before_intro et on n'a jamais trouvé l'intro : tout va dans rest
            rest_lines.append(line)
    return "\n".join(intro_lines).strip(), "\n".join(rest_lines).strip()


_DEFINITION_QUESTION_PATTERNS = (
    # FR
    "qu'est-ce que", "qu'est-ce qu'", "quelle est la définition", "quelle est la definition",
    "que désigne", "que designe", "que signifie", "quel est le but", "quel est l'objectif",
    "comment définit-on", "comment definit-on", "que représente", "que represente",
    # AR
    "ما هو", "ما هي", "ما تعريف", "ما المقصود",
    # autres formes paresseuses
    "toutes les réponses sont correctes", "toutes les reponses sont correctes",
    "كل الإجابات صحيحة",
)


def _is_tautological_quiz_question(question: str, correct_option_text: str) -> bool:
    """Détecte un QCM tautologique. Deux signaux :

    1. La question est une *demande de définition* (pattern "Qu'est-ce que X ?")
       → quasi-systématiquement tautologique car la bonne réponse = la définition.
    2. La bonne réponse contient ≥ 40 % des tokens significatifs de la question
       (overlap lexical élevé).

    Aussi : flag les questions où l'option correcte est "Toutes les réponses sont correctes" (paresse).
    """
    q_norm = (question or "").lower().strip()
    a_norm = (correct_option_text or "").lower().strip()

    if not q_norm or not a_norm:
        return False

    # Signal 1 : question définitionnelle
    for pat in _DEFINITION_QUESTION_PATTERNS:
        if pat in q_norm or pat in a_norm:
            return True

    # Signal 2 : overlap lexical
    stop = {
        "de", "des", "du", "la", "le", "les", "un", "une", "et", "ou", "en", "au", "aux",
        "à", "qu", "ce", "que", "qui", "est", "sont", "dans", "pour", "par", "sur",
        "the", "a", "an", "of", "to", "in", "for", "on", "is", "are", "what",
        "هو", "هي", "ما", "في", "من", "إلى", "على",
    }

    def _tokens(s: str) -> set[str]:
        words = re.findall(r"[\w؀-ۿ]+", s, re.UNICODE)
        return {w for w in words if len(w) > 2 and w not in stop}

    q_tok = _tokens(q_norm)
    a_tok = _tokens(a_norm)
    if not a_tok or not q_tok:
        return False
    overlap = q_tok & a_tok
    # Seuil 60 % : on ne flag que les vraies tautologies. Les patterns définitionnels (signal 1)
    # restent attrapés indépendamment.
    return (len(overlap) / len(a_tok)) >= 0.60


def _filter_tautological_quiz(quiz_md: str) -> tuple[str, int, int]:
    """Parse le quiz markdown, retire les questions tautologiques.

    Retourne ``(md_propre, n_drop, n_kept_with_correct)``.
    Le 3ᵉ champ est le nb de questions valides restantes — utile pour décider d'un retry.
    """
    if not quiz_md:
        return quiz_md, 0, 0
    blocks = re.split(r"\n(?=\s*(?:\*\*)?Q?\d+[\.\)\:])", quiz_md)
    kept: list[str] = []
    dropped = 0
    kept_questions = 0
    for block in blocks:
        if not block.strip():
            kept.append(block)
            continue
        m_correct = re.search(r"^\s*[A-D]\)\s*(.+?)\s*✅", block, re.MULTILINE)
        if not m_correct:
            kept.append(block)
            continue
        first_line = next((ln for ln in block.splitlines() if ln.strip()), "")
        question_text = first_line.lstrip("*0123456789. )Q:").strip()
        correct_text = m_correct.group(1).strip()
        if _is_tautological_quiz_question(question_text, correct_text):
            logger.info("Quiz tautologique retiré : %r → %r", question_text[:80], correct_text[:60])
            dropped += 1
            continue
        kept.append(block)
        kept_questions += 1
    return "\n".join(kept), dropped, kept_questions


def _replace_quiz_section(annexes_md: str, new_quiz_md: str) -> str:
    """Remplace la section ## Quiz dans annexes_md par ``new_quiz_md`` (préfixé d'un titre)."""
    if not annexes_md:
        return new_quiz_md
    lines = annexes_md.splitlines()
    out: list[str] = []
    state = "before"  # before, in_quiz, after_quiz
    quiz_title_kw = ("quiz", "qcm", "اختبار", "أسئلة")
    for line in lines:
        if line.startswith("## ") or line.startswith("# "):
            head = line.lstrip("#").strip().lower()
            is_quiz = any(k in head for k in quiz_title_kw) and "flashcard" not in head and "بطاقات" not in head
            if state == "before" and is_quiz:
                state = "in_quiz"
                out.append(line)  # garde le titre
                out.append(new_quiz_md.strip())
                continue
            if state == "in_quiz":
                state = "after_quiz"
                out.append(line)
                continue
        if state != "in_quiz":
            out.append(line)
    if state == "before":
        # Pas trouvé : on append à la fin avec un titre.
        out.append("\n## Quiz d'entraînement\n\n" + new_quiz_md.strip())
    return "\n".join(out)


def _strip_redundant_definition_subsections(markdown_text: str) -> str:
    """Retire les sous-sections H3 dont le titre est 'Définition de X' / 'Definition of X'.

    Ces sous-sections sont quasi-systématiquement redondantes (recopient l'ouverture H2).
    Le corps de la sous-section est conservé mais fusionné dans la H2 parent — en pratique
    on le garde juste collé après l'ouverture sans titre H3.
    """
    if not markdown_text:
        return markdown_text
    out_lines: list[str] = []
    skip_h3_line = False
    pattern = re.compile(r"^\s*###\s*(?:Définition|Definition|تعريف)\s+(?:de|d'|of|des|du|للـ)\s", re.IGNORECASE)
    for line in markdown_text.splitlines():
        if pattern.match(line):
            skip_h3_line = True
            continue
        out_lines.append(line)
    if skip_h3_line:
        logger.info("Sous-sections '### Définition de X' redondantes retirées")
    return "\n".join(out_lines)


def _build_quiz_retry_prompt_fr(expl_label: str) -> str:
    """Prompt très strict pour regénérer UNIQUEMENT le quiz quand la 1ʳᵉ passe a produit
    trop de tautologies."""
    return f"""Tu es un *examinateur*. Crée *5 QCM* sur le cours fourni (transcript).

⚠️ RÈGLES ABSOLUES — chaque violation = échec :
1. INTERDIT de poser une question définitionnelle : pas de "Qu'est-ce que X ?", "Que désigne X ?", "Quel est le but de X ?".
2. INTERDIT que la bonne réponse contienne les mots-clés de la question.
3. INTERDIT l'option "Toutes les réponses sont correctes".
4. CHAQUE question doit forcer : un calcul, une distinction de cas, une application, ou un choix entre concepts proches.

✏️ Format exact (5 questions) :
**1.** Énoncé qui force une réflexion (idéalement avec données chiffrées)
- A) Option distractrice plausible
- B) Option correcte ✅
- C) Option distractrice plausible
- D) Option distractrice plausible
*{expl_label} :* 1 phrase explicative justifiant pourquoi B est correct (et pas les autres)

(répéter pour 5 questions)

❌ Exemples NULS à NE PAS imiter :
- "Qu'est-ce que la moyenne ? → la valeur moyenne" (tautologie)
- "Quel est le but du test d'hypothèse ? → évaluer une hypothèse" (définition)
- "Toutes les réponses sont correctes" (paresse)

✅ Exemples BONS :
- "Sur (1, 2, 3, 4, 100), pourquoi la médiane reflète mieux le centre ? → 100 est un outlier qui tire la moyenne"
- "Si covariance = -2 et écarts-types 1 et 5, r vaut ? → -0.4"
- "Quel test choisir pour comparer 3 moyennes de groupes indépendants ? → ANOVA"

Réponds UNIQUEMENT par les 5 QCM en français. Pas d'intro, pas de conclusion."""


def _build_quiz_retry_prompt_ar(expl_label: str) -> str:
    return f"""أنت ممتحن. أنشئ *5 أسئلة QCM* عن الدرس المقدم.

⚠️ قواعد مطلقة:
1. ممنوع أسئلة تعريفية ("ما هو X؟").
2. ممنوع أن تحوي الإجابة الصحيحة كلمات السؤال.
3. ممنوع "كل الإجابات صحيحة".
4. كل سؤال يجب أن يفرض: حساب، أو تمييز، أو تطبيق، أو اختيار بين مفاهيم.

التنسيق:
**1.** السؤال
- A) ...
- B) ... ✅
- C) ...
- D) ...
*{expl_label}:* جملة

(5 أسئلة)

أجب فقط بالـ 5 أسئلة بالعربية."""


def _detect_subject_from_text(transcript: str) -> Optional[str]:
    """Heuristique simple : premier groupe de mots-clés qui matche le début du transcript."""
    if not transcript:
        return None
    head = transcript[:1500].lower()
    for keywords, label in _SUBJECT_KEYWORDS:
        for kw in keywords:
            if kw in head:
                return label
    return None


_QUIZ_STOPWORDS = {
    "de", "des", "du", "la", "le", "les", "un", "une", "et", "ou", "en", "au", "aux",
    "à", "qu", "ce", "que", "qui", "est", "sont", "dans", "pour", "par", "sur", "se",
    "ne", "pas", "plus", "moins", "très", "tres", "ses", "son", "sa", "tels", "telle",
    "the", "a", "an", "of", "to", "in", "for", "on", "is", "are", "what",
    "هو", "هي", "ما", "في", "من", "إلى", "على",
}


def _quiz_tokens(text: str) -> set[str]:
    """Tokens significatifs (>2 chars, hors stop) pour comparer options et explication."""
    s = (text or "").lower()
    words = re.findall(r"[\w؀-ۿ]+", s, re.UNICODE)
    return {w for w in words if len(w) > 2 and w not in _QUIZ_STOPWORDS}


def _resolve_correct_option(
    options: list[tuple[str, str, bool]], explanation: str
) -> tuple[Optional[str], bool]:
    """Détermine la bonne réponse en croisant ✅ et l'overlap discriminant explication↔option.

    On ne compare pas les tokens *bruts* des options à l'explication (trop bruité : "mesure",
    "variable" reviennent partout) mais les *tokens discriminants* — ceux qui n'apparaissent
    que dans UNE option. Ça isole le contenu spécifique de chaque réponse.

    Retourne ``(letter, overridden)``. ``letter=None`` si non résolvable.
    ``overridden=True`` si on a corrigé le ✅ du LLM (cas covariance : LLM ✅ A "dispersion"
    mais explication décrit "deux variables" → on override vers B).
    """
    if not options:
        return None, False

    marked = next((let for let, _, ok in options if ok), None)
    expl_tok = _quiz_tokens(explanation)
    if not expl_tok:
        return marked, False  # pas d'explication exploitable → on fait confiance au ✅

    # Tokens par option, puis identification des tokens partagés (≥ 2 options)
    opt_tok = {letter: _quiz_tokens(txt) for letter, txt, _ in options}
    shared: set[str] = set()
    letters = list(opt_tok.keys())
    for i, l1 in enumerate(letters):
        for l2 in letters[i + 1:]:
            shared |= opt_tok[l1] & opt_tok[l2]
    # Tokens discriminants = uniques à une seule option
    discriminative = {let: tok - shared for let, tok in opt_tok.items()}

    # Score = overlap des tokens discriminants avec l'explication
    scored: dict[str, float] = {}
    for let, disc in discriminative.items():
        scored[let] = (len(disc & expl_tok) / len(disc)) if disc else 0.0

    best_letter = max(scored, key=lambda l: scored[l])
    best_score = scored[best_letter]
    marked_score = scored.get(marked, 0.0) if marked else 0.0

    if marked is None:
        # Pas de ✅ → on prend le meilleur si signal exploitable (≥ 30 %)
        return (best_letter if best_score >= 0.30 else None), False

    # Override si :
    # (a) marked a 0 token discriminant dans l'explication, mais best en a au moins quelques-uns ;
    # OU
    # (b) écart d'overlap discriminant ≥ 30 points en faveur d'une autre option.
    if best_letter != marked and best_score > 0:
        if marked_score == 0.0:
            return best_letter, True
        if (best_score - marked_score) >= 0.30:
            return best_letter, True

    return marked, False


def _extract_quiz(lesson_md: str) -> list[dict[str, Any]]:
    """Extrait les QCM du markdown. Retour : liste de {question, options, correct, explanation}.

    Robustesse :
      - Accepte les options avec préfixe bullet (`- A)`, `* A)`) ou sans (`A)`).
      - Cross-check ✅ vs explication : si le LLM s'est trompé de marquage, on corrige.
      - Skip les questions où la bonne réponse n'est pas identifiable (mieux que feedback faux).
    """
    if not lesson_md:
        return []
    lines = lesson_md.splitlines()
    questions: list[dict[str, Any]] = []
    # Accepte bullet prefix optionnel : "- A)", "* A)", "+ A)", "A)"
    opt_re = re.compile(r"^\s*(?:[-*+]\s+)?([A-D])\)\s*(.+?)\s*$")
    # Accepte markup italic/bold : "*Explication :*", "**Explication:**", etc.
    expl_re = re.compile(
        r"^\s*\*{0,2}\s*(?:Explication|تفسير|Réponse|Reponse)\s*\*{0,2}\s*:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$",
        re.IGNORECASE,
    )

    def _clean_question(raw: str) -> str:
        s = raw or ""
        # Retire markup leading : **, *, #, spaces
        s = re.sub(r"^[\*#\s]+", "", s)
        # Retire numérotation : "1.", "1)", "1:", "Q1.", "Question 1:", + éventuels **
        s = re.sub(r"^(?:Question\s+)?(?:Q\s*)?\d+\s*[\.\)\:]?\s*\*{0,2}\s*", "", s, flags=re.IGNORECASE)
        # Retire markup résiduel
        s = re.sub(r"^[\*#\s]+", "", s).strip("* ").strip()
        return s

    i = 0
    while i < len(lines):
        if opt_re.match(lines[i]):
            qline = ""
            j = i - 1
            while j >= 0:
                cand = lines[j].strip().lstrip("#").strip()
                if cand and not re.match(r"^\s*\*{0,2}\s*(?:Explication|تفسير|Réponse|Reponse)", cand, re.IGNORECASE):
                    qline = _clean_question(cand)
                    break
                j -= 1

            options: list[tuple[str, str, bool]] = []
            while i < len(lines):
                m = opt_re.match(lines[i])
                if not m:
                    break
                letter = m.group(1)
                txt = m.group(2)
                is_correct = "✅" in txt
                clean = txt.replace("✅", "").strip().strip("*").strip()
                options.append((letter, clean, is_correct))
                i += 1

            explanation = ""
            k = i
            while k < len(lines) and k < i + 4:
                em = expl_re.match(lines[k])
                if em:
                    explanation = em.group(1).strip()
                    break
                k += 1

            if qline and len(options) >= 2:
                resolved, overridden = _resolve_correct_option(options, explanation)
                if resolved is None:
                    # Question non résolvable (pas de ✅, pas d'explication exploitable) → skip
                    logger.info(
                        "Quiz : question non résolvable, skippée — Q: %r",
                        qline[:80],
                    )
                    continue
                if overridden:
                    marked = next((let for let, _, ok in options if ok), "?")
                    logger.warning(
                        "Quiz : bonne réponse corrigée par cross-check explication (LLM marqué %s, on prend %s) — Q: %r",
                        marked, resolved, qline[:80],
                    )
                questions.append({
                    "question": qline[:600],
                    "options": [(let, txt) for let, txt, _ in options],
                    "correct": resolved,
                    "explanation": explanation[:600],
                })
            continue
        i += 1
    return questions[:10]


def _audio_ext_from_mime(mime: Optional[str]) -> str:
    if not mime:
        return ".ogg"  # WhatsApp voice par défaut = audio/ogg; codecs=opus
    m = mime.lower()
    if "ogg" in m:
        return ".ogg"
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "wav" in m:
        return ".wav"
    if "mp4" in m or "m4a" in m or "aac" in m:
        return ".m4a"
    if "amr" in m:
        return ".amr"
    return ".ogg"


async def _safe_send(to_phone: str, body: str) -> None:
    """Send qui n'explose pas si Meta refuse — on log et on continue."""
    try:
        await wa_client.send_text(to_phone, body)
    except Exception:
        logger.exception("send_text échoué pour %s", to_phone)


def _wa_user_model_id(user: User) -> str:
    """Modèle WhatsApp pref de l'user, validé contre le catalogue actuel.

    Si la préférence stockée n'est plus dans ``RETAIL_MODELS`` (modèle retiré, ex. ``local``/eco),
    on retourne le défaut sans planter — la prochaine commande ``/modele`` corrigera durablement.
    """
    from transcription_retail_catalog import RETAIL_MODELS

    stored = getattr(user, "whatsapp_transcription_model", None)
    if stored and stored in RETAIL_MODELS:
        return stored
    return _WA_DEFAULT_MODEL


async def _handle_modele_command(db: Session, user: User, phone: str, ui_loc: str, cmd: str) -> None:
    """Affiche ou change le modèle de transcription WhatsApp préféré du user."""
    from transcription_retail_catalog import RETAIL_MODELS

    parts = cmd.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    def _label(model_id: str) -> str:
        spec = RETAIL_MODELS.get(model_id)
        if spec is None:
            return model_id
        return (spec.label_ar if ui_loc.startswith("ar") else spec.label_fr) or model_id

    def _price(model_id: str) -> str:
        spec = RETAIL_MODELS.get(model_id)
        return f"{spec.mru_nouveau:g}" if spec else "?"

    if not arg:
        current_id = _wa_user_model_id(user)
        await _safe_send(
            phone,
            t(
                "modele_list",
                ui_loc,
                current_label=_label(current_id),
                current_mru=_price(current_id),
                p_turbo=_price("whisper-large-v3-turbo"),
                p_large=_price("whisper-large-v3"),
                p_4omini=_price("gpt-4o-mini-transcribe"),
                p_w1=_price("whisper-1"),
            ),
        )
        return

    target_id = _WA_MODEL_ALIASES.get(arg) or (arg if arg in RETAIL_MODELS else None)
    if target_id is None:
        await _safe_send(phone, t("modele_unknown", ui_loc, alias=arg))
        return

    user.whatsapp_transcription_model = target_id
    db.commit()
    await _safe_send(
        phone,
        t("modele_set_ok", ui_loc, label=_label(target_id), mru=_price(target_id)),
    )
    await _maybe_hint(phone, "hint_after_modele_set", ui_loc)


def _is_rate_limited(phone: str) -> bool:
    now = time.monotonic()
    last = _last_audio_at.get(phone, 0.0)
    if now - last < _RATE_LIMIT_SECONDS:
        return True
    _last_audio_at[phone] = now
    return False


async def handle_inbound(msg: InboundMessage) -> None:
    """Point d'entrée appelé depuis le webhook en background task."""
    if not msg or not msg.wa_id:
        return

    db: Session = SessionLocal()
    try:
        await asyncio.wait_for(_dispatch(db, msg), timeout=_PROCESS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("WhatsApp process timeout msg_id=%s phone=%s", msg.message_id, msg.wa_id)
        await _safe_send(
            msg.e164_phone,
            t("transcribe_failed_with_reason", "fr",
              reason=f"Délai dépassé ({int(_PROCESS_TIMEOUT_SECONDS)}s) — audio trop long ou worker saturé."),
        )
    except Exception as exc:
        logger.exception("WhatsApp dispatch exception msg_id=%s", msg.message_id)
        reason = f"Erreur interne : {type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(msg.e164_phone, t("transcribe_failed_with_reason", "fr", reason=reason))
    finally:
        db.close()


async def _run_audio_pipeline_for_user(
    *,
    phone: str,
    ui_loc: str,
    user_id: int,
    public_id: str,
    rel_path: str,
    mime: Optional[str],
    message_id: str,
    initial_subject: str,
    explicit_subject: bool,
) -> None:
    """Crée le ``TranscriptionJob``, lance la pipeline, attend, génère la leçon, envoie PDF + partage.

    Appelé soit directement (matière déjà connue), soit après que l'user a répondu à la question
    de matière (``_pending_audio[phone]`` consommé).

    ``explicit_subject=True`` empêche l'auto-détection après transcription (l'user a choisi).
    """
    speech_lang = "ar" if ui_loc.startswith("ar") else "fr"
    ext = Path(rel_path).suffix or ".ogg"

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            logger.warning("User %s disparu avant transcription public_id=%s", user_id, public_id)
            return
        job = TranscriptionJob(
            public_id=public_id,
            user_id=user_id,
            original_filename=f"whatsapp_{message_id[:24]}{ext}"[:384],
            subject=initial_subject or "General",
            speech_language=speech_lang,
            ui_locale=ui_loc[:16],
            transcription_engine=_wa_user_model_id(user),
            input_relpath=rel_path,
            client_content_type=(mime or "")[:160] or None,
            status="queued",
            progress_percent=1,
            phase="received",
            status_message="Reçu via WhatsApp — en file d'attente.",
            source="whatsapp",
            whatsapp_phone=phone,
            whatsapp_message_id=message_id,
        )
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing_job = db.execute(
                select(TranscriptionJob).where(TranscriptionJob.whatsapp_message_id == message_id)
            ).scalar_one_or_none()
            if existing_job is None:
                logger.exception("IntegrityError sans job existant — abandon msg_id=%s", message_id)
                return
            logger.info("WhatsApp duplicate (race) ignored msg_id=%s public_id=%s", message_id, existing_job.public_id)
            return
        db.refresh(job)
        model_label = _wa_user_model_id(user)
    finally:
        db.close()

    from routes import transcribe_jobs as tj

    asyncio.create_task(tj.execute_transcription_job(public_id))

    # Astuce affichée 1 fois : rappelle /matiere et /langue dès le premier audio.
    await _maybe_hint(phone, "hint_after_ack", ui_loc)
    await _safe_send(phone, t("progress_transcribing", ui_loc, model=model_label))

    # Livraison détachée : la tâche tourne indépendamment du timeout du dispatcher (Meta webhook
    # qui plafonne à `_PROCESS_TIMEOUT_SECONDS`). Sa propre limite est `_DELIVERY_TIMEOUT_SECONDS`
    # (défaut 2h) pour couvrir un Whisper local long.
    asyncio.create_task(
        _deliver_lesson_after_transcription(
            phone=phone, ui_loc=ui_loc, public_id=public_id, explicit_subject=explicit_subject
        )
    )


async def _deliver_lesson_after_transcription(
    *, phone: str, ui_loc: str, public_id: str, explicit_subject: bool
) -> None:
    """Tâche détachée : attend la fin de la transcription puis envoie un *menu* de commandes.

    Volontairement passive après la transcription : l'user choisit ce qu'il veut consommer
    (/pdf, /texte, /quiz, /partage), facturé **à la commande**.
    """
    try:
        final_job = await _wait_for_transcription(public_id, timeout_seconds=_DELIVERY_TIMEOUT_SECONDS)
        if final_job is None or final_job.status != "done":
            err = (final_job.error_detail if final_job else None) or "Aucune réponse du worker (timeout)."
            logger.warning("Transcription WhatsApp failed public_id=%s err=%s", public_id, err)
            await _safe_send(phone, t("transcribe_failed_with_reason", ui_loc, reason=str(err)[:300]))
            await _maybe_hint(phone, "hint_after_transcribe_fail", ui_loc)
            return

        # Auto-détection matière uniquement si l'user n'a pas explicitement précisé.
        if not explicit_subject and (final_job.subject or "General") == "General":
            import json as _json
            try:
                payload = _json.loads(final_job.result_json) if final_job.result_json else {}
            except Exception:
                payload = {}
            transcript_for_detect = (payload.get("transcript") or "") if isinstance(payload, dict) else ""
            detected = _detect_subject_from_text(transcript_for_detect)
            if detected:
                db2 = SessionLocal()
                try:
                    jj = db2.get(TranscriptionJob, final_job.id)
                    if jj is not None:
                        jj.subject = detected
                        db2.add(jj)
                        db2.commit()
                        final_job.subject = detected
                finally:
                    db2.close()

        # Menu post-transcription : on annonce la disponibilité et on liste les commandes
        # avec leur tarif. Chacune sera débitée au moment où l'user la lance.
        subject = final_job.subject or "General"
        await _safe_send(phone, t("transcription_ready", ui_loc, subject=subject))
        await _safe_send(phone, t("menu_after_transcription", ui_loc))
    except Exception as exc:
        logger.exception("Tâche post-transcription WhatsApp a planté public_id=%s", public_id)
        reason = f"{type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(phone, t("generate_failed_with_reason", ui_loc, reason=reason))


async def _dispatch(db: Session, msg: InboundMessage) -> None:
    phone = msg.e164_phone
    # Lookup user — la colonne ``whatsapp_phone`` est unique + indexée.
    user = db.execute(select(User).where(User.whatsapp_phone == phone)).scalar_one_or_none()
    locale_hint = wa_config.default_language()

    # === Cas 1 : numéro inconnu ===
    if user is None:
        signup = wa_config.signup_url()
        if signup:
            await _safe_send(phone, t("welcome_unknown", locale_hint, signup_url=signup))
        else:
            await _safe_send(phone, t("welcome_unknown_no_url", locale_hint))
        return

    # On utilise dorénavant la locale du user si possible. Faute de colonne ``ui_locale`` sur User,
    # on lit la dernière transcription du user pour deviner (sinon défaut env).
    ui_loc = _guess_user_locale(db, user) or locale_hint

    # === Réponse interactive (boutons / liste) ===
    if msg.type == "interactive" and msg.interactive_id:
        rid = msg.interactive_id
        if rid == "quiz:start":
            await _handle_quiz_command(user, phone, ui_loc)
            return
        if rid.startswith("quiz:"):
            await _handle_quiz_reply(phone, rid)
            return
        # Réponse interactive non reconnue — on guide.
        await _safe_send(phone, t("help_text", ui_loc))
        return

    # === Commandes texte ===
    if msg.type == "text" and msg.text:
        cmd_raw = msg.text.strip()
        cmd = cmd_raw.lower()

        # === Réponse à une question de matière en attente ? ===
        pending = _pending_audio.get(phone)
        if pending is not None:
            # Expiration → on jette l'audio staged, on traite le message comme commande normale.
            if (time.monotonic() - float(pending.get("asked_at") or 0)) > _PENDING_AUDIO_TTL_SECONDS:
                _cleanup_pending_audio(phone, delete_file=True)
                await _safe_send(phone, t("pending_expired", ui_loc))
                # fall through pour traiter le message comme commande normale
            elif cmd in ("/skip", "skip", "passer", "/passer", "تخطي", "/تخطي"):
                _cleanup_pending_audio(phone, delete_file=False)
                await _safe_send(phone, t("matiere_skipped", ui_loc))
                await _run_audio_pipeline_for_user(
                    phone=phone,
                    ui_loc=pending["ui_loc"],
                    user_id=pending["user_id"],
                    public_id=pending["public_id"],
                    rel_path=pending["rel_path"],
                    mime=pending.get("mime"),
                    message_id=pending["message_id"],
                    initial_subject="General",
                    explicit_subject=False,
                )
                return
            elif not cmd.startswith("/"):
                subject = cmd_raw[:128]
                _cleanup_pending_audio(phone, delete_file=False)
                await _safe_send(phone, t("matiere_received", ui_loc, subject=subject))
                await _run_audio_pipeline_for_user(
                    phone=phone,
                    ui_loc=pending["ui_loc"],
                    user_id=pending["user_id"],
                    public_id=pending["public_id"],
                    rel_path=pending["rel_path"],
                    mime=pending.get("mime"),
                    message_id=pending["message_id"],
                    initial_subject=subject,
                    explicit_subject=True,
                )
                return
            else:
                # Autre commande (/aide, /solde…) tapée pendant qu'un audio est en attente :
                # on annule le pending et on traite la commande normalement.
                _cleanup_pending_audio(phone, delete_file=True)
                await _safe_send(phone, t("pending_cancelled", ui_loc))
                # fall through

        if cmd in ("/aide", "/help", "/start", "aide", "help", "/menu"):
            await _safe_send(phone, t("help_text", ui_loc))
            return
        if cmd in ("/solde", "/balance", "solde", "balance"):
            mru = wallet_units_to_mru_display(int(user.credit_balance or 0))
            await _safe_send(phone, t("balance_line", ui_loc, mru=f"{mru:.2f}"))
            if mru < 50:
                topup = wa_config.topup_url()
                if topup:
                    await _maybe_hint(phone, "hint_balance_low", ui_loc, topup_url=topup)
            return
        if cmd.startswith("/modele") or cmd.startswith("/model") or cmd == "modele":
            await _handle_modele_command(db, user, phone, ui_loc, cmd)
            return
        if cmd.startswith("/matiere") or cmd.startswith("/matière") or cmd.startswith("/subject"):
            await _handle_matiere_command(db, user, phone, ui_loc, cmd)
            return
        if cmd.startswith("/langue") or cmd.startswith("/lang") or cmd.startswith("/language"):
            await _handle_langue_command(db, user, phone, ui_loc, cmd)
            return
        if cmd in ("/pdf", "/fiche", "/cours", "pdf"):
            await _handle_pdf_command(user, phone, ui_loc)
            return
        if cmd in ("/partage", "/share", "/lien", "partage"):
            await _handle_partage_command(user, phone, ui_loc)
            return
        if cmd in ("/refaire", "/regen", "/retry", "refaire"):
            await _handle_refaire_command(user, phone, ui_loc)
            return
        if cmd in ("/quiz", "quiz"):
            await _handle_quiz_command(user, phone, ui_loc)
            return
        if cmd in ("/texte", "/text", "/transcript", "/transcription", "texte"):
            await _handle_texte_command(user, phone, ui_loc)
            return
        # Texte libre non reconnu — astuce courte 1ʳᵉ fois, sinon help_text complet.
        seen = _hints_seen.setdefault(phone, set())
        if "hint_unknown_command" not in seen:
            await _maybe_hint(phone, "hint_unknown_command", ui_loc)
        else:
            await _safe_send(phone, t("help_text", ui_loc))
        return

    # === Audio / voice / document audio ===
    if msg.type not in ("audio", "voice", "document") or not msg.media_id:
        await _safe_send(phone, t("unsupported_type", ui_loc))
        return

    # === Idempotence : si le même wamid a déjà été traité, no-op ===
    existing = db.execute(
        select(TranscriptionJob).where(TranscriptionJob.whatsapp_message_id == msg.message_id)
    ).scalar_one_or_none()
    if existing is not None:
        logger.info("WhatsApp duplicate message_id ignored: %s", msg.message_id)
        return

    # === Rate-limit ===
    if _is_rate_limited(phone):
        await _safe_send(phone, t("rate_limited", ui_loc))
        return

    # === Wallet block ===
    block = wallet_block_reason(user)
    if block:
        topup = wa_config.topup_url()
        if topup:
            await _safe_send(phone, t("wallet_blocked", ui_loc, topup_url=topup))
        else:
            await _safe_send(phone, t("wallet_blocked_no_url", ui_loc))
        return

    # === ACK immédiat ===
    await _safe_send(phone, t("ack_audio", ui_loc))

    # === Download du média Meta (URL valable ~5 min, on capture tout de suite) ===
    public_id = uuid.uuid4().hex
    ext = _audio_ext_from_mime(msg.media_mime)
    jdir = _DATA / "jobs" / public_id
    jdir.mkdir(parents=True, exist_ok=True)
    rel = Path("jobs") / public_id / f"upload{ext}"
    abs_path = _DATA / rel

    try:
        await wa_client.download_media(msg.media_id, abs_path)
    except Exception as e:
        logger.exception("download_media failed wamid=%s", msg.message_id)
        msg_text = str(e)
        if "trop volumineux" in msg_text.lower():
            await _safe_send(phone, t("media_too_large", ui_loc))
        else:
            reason = f"{type(e).__name__} — {msg_text[:200]}"
            await _safe_send(phone, t("download_failed_with_reason", ui_loc, reason=reason))
        return

    preset = (getattr(user, "whatsapp_subject", None) or "").strip()
    if preset:
        # Matière fixée explicitement via /matiere → pas de question, on enchaîne.
        await _run_audio_pipeline_for_user(
            phone=phone,
            ui_loc=ui_loc,
            user_id=user.id,
            public_id=public_id,
            rel_path=str(rel.as_posix()),
            mime=msg.media_mime,
            message_id=msg.message_id,
            initial_subject=preset,
            explicit_subject=True,
        )
        return

    # Pas de préférence → on demande la matière et on stocke l'audio en attente.
    # Annule un éventuel pending précédent (et son fichier orphelin).
    prev = _cleanup_pending_audio(phone, delete_file=True)
    if prev is not None:
        logger.info("WhatsApp pending audio replaced for %s", phone)

    _pending_audio[phone] = {
        "public_id": public_id,
        "rel_path": str(rel.as_posix()),
        "mime": msg.media_mime,
        "message_id": msg.message_id,
        "ui_loc": ui_loc,
        "user_id": user.id,
        "asked_at": time.monotonic(),
    }
    await _safe_send(phone, t("ask_matiere", ui_loc))
    # Auto-skip après N secondes si l'user n'a pas répondu — tâche détachée du dispatcher.
    if _PENDING_AUDIO_AUTO_SKIP_SECONDS > 0:
        asyncio.create_task(
            _auto_skip_pending_audio_after(phone, public_id, _PENDING_AUDIO_AUTO_SKIP_SECONDS)
        )


async def _wait_for_transcription(public_id: str, timeout_seconds: Optional[float] = None) -> Optional[TranscriptionJob]:
    """Poll la DB jusqu'à statut terminal (done/failed/cancelled)."""
    elapsed = 0.0
    limit = timeout_seconds if timeout_seconds is not None else _PROCESS_TIMEOUT_SECONDS
    while elapsed < limit:
        await asyncio.sleep(_TRANSCRIBE_POLL_INTERVAL)
        elapsed += _TRANSCRIBE_POLL_INTERVAL
        db = SessionLocal()
        try:
            job = db.execute(
                select(TranscriptionJob).where(TranscriptionJob.public_id == public_id)
            ).scalar_one_or_none()
            if job is None:
                return None
            if job.status in ("done", "failed", "cancelled"):
                return job
        finally:
            db.close()
    return None


async def _build_lesson_for_job(job: TranscriptionJob) -> tuple[Optional[str], Optional[str]]:
    """Appelle ``run_course_pipeline`` à partir des annotations ASR stockées dans result_json.

    Retourne ``(lesson_markdown_or_None, error_reason_or_None)``. La 2ᵉ valeur est une chaîne
    courte (≤ ~200 chars) prête à être insérée dans un message WhatsApp si la génération a échoué.
    """
    import json as _json

    from credits_wallet import debit_credits
    from pricing import billed_mru_to_wallet_units_debit, groq_billed

    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not api_key:
        logger.error("GROQ_API_KEY manquant — impossible de générer la leçon WhatsApp.")
        return None, "Clé API LLM (GROQ_API_KEY) absente côté serveur."

    payload = {}
    try:
        if job.result_json:
            payload = _json.loads(job.result_json)
    except Exception:
        logger.exception("result_json malformé public_id=%s", job.public_id)

    transcript = payload.get("transcript") or payload.get("timestamped_transcript") or ""
    asr = payload.get("asr_passages_annotated") if isinstance(payload, dict) else None
    mixed = payload.get("transcript_mixed_view") if isinstance(payload, dict) else None

    if not transcript or len(transcript.strip()) < 50:
        logger.warning("Transcript trop court pour génération public_id=%s", job.public_id)
        return None, "Transcription trop courte pour générer une fiche (audio < 30s ou silencieux ?)."

    target_lang = "ar" if (job.ui_locale or "").startswith("ar") else "fr"
    lang_name = "Arabic" if target_lang == "ar" else "French"
    expl_label = "تفسير" if target_lang == "ar" else "Explication"

    # Prompt WhatsApp dédié : prioritise le **corps du cours** (le PDF strippe quiz/flashcards).
    # Cible de longueur agressive — ~70 % des mots du transcript, plancher 1200, plafond 12000.
    transcript_words_estimate = max(1, len(transcript.split()))
    target_words_body = max(1200, min(int(transcript_words_estimate * 0.70), 12000))

    if target_lang == "ar":
        prompt = _build_lesson_prompt_ar(target_words_body)
    else:
        prompt = _build_lesson_prompt_fr(target_words_body)
    prompt += (
        f"\n\nDernière contrainte : écris tout en {lang_name}. "
        + "Quiz : options A) B) C) D) (Latin letters), bonne réponse marquée ✅, "
        + f"explication préfixée par '{expl_label}:'."
    )
    logger.info(
        "Lesson prompt WhatsApp : target body=%d mots (transcript ~%d mots)",
        target_words_body, transcript_words_estimate,
    )

    model = (os.getenv("GROQ_GENERATE_MODEL") or "").strip() or "llama-3.3-70b-versatile"

    # Budget de tokens adaptatif : ~1 token = 4 chars (approx). On vise une fiche dont la longueur
    # est proportionnelle au transcript (~60 % du nombre de tokens d'entrée), avec un plancher et
    # un plafond. Un cours de 5 min ≈ 600 tokens fiche, 1h ≈ 6000-7000 tokens, 2h ≈ 12000 max.
    transcript_chars = len(transcript)
    estimated_input_tokens = transcript_chars // 4
    adaptive = int(estimated_input_tokens * 0.6)
    # Plancher 1500 (assez pour 1 mini-cours), plafond configurable (par défaut 16k).
    try:
        max_tokens_cap = int((os.getenv("GROQ_GENERATE_MAX_TOKENS_CAP") or "16000").strip())
    except ValueError:
        max_tokens_cap = 16000
    max_tokens = max(1500, min(adaptive, max_tokens_cap))
    logger.info(
        "Lesson budget adaptive : transcript=%d chars (~%d tokens) → max_tokens=%d (cap %d)",
        transcript_chars, estimated_input_tokens, max_tokens, max_tokens_cap,
    )

    # =========================================================================
    # Génération section par section : plan JSON → N appels LLM (1 par section) → annexes.
    # Forte garantie de longueur car chaque section a sa propre cible mots strictement appliquée.
    # =========================================================================
    import json as _json

    from groq import Groq
    from course_from_transcript import _groq_chat

    client = Groq(api_key=api_key)

    # Pass 1 : plan JSON (structuré, parsable)
    plan_system = _build_plan_json_prompt_ar() if target_lang == "ar" else _build_plan_json_prompt_fr()
    plan_user = f"Matière : {job.subject or 'General'}\n\nTRANSCRIPT :\n{transcript}"

    try:
        plan_raw, p_inp, p_out = await asyncio.to_thread(
            _groq_chat,
            client,
            model=model,
            system=plan_system,
            user=plan_user,
            max_tokens=2500,
            temperature=0.3,
            extra_create_kwargs={"response_format": {"type": "json_object"}},
        )
    except Exception as exc:
        logger.exception("Pass plan (JSON) Groq a échoué public_id=%s", job.public_id)
        return None, f"LLM plan : {type(exc).__name__} — {str(exc)[:160]}"

    try:
        plan_data = _json.loads(plan_raw or "{}")
    except Exception:
        logger.warning("Plan JSON invalide public_id=%s — retombe sur génération simple", job.public_id)
        plan_data = {}

    sections = plan_data.get("sections") if isinstance(plan_data, dict) else None
    glossary_terms = plan_data.get("glossary_terms") if isinstance(plan_data, dict) else None
    if not isinstance(sections, list) or len(sections) < 2:
        logger.warning("Plan JSON sans sections exploitables — fallback single-pass public_id=%s", job.public_id)
        # Fallback : un seul appel LLM avec le prompt monolithique
        prompt_fallback = _build_lesson_prompt_ar(target_words_body) if target_lang == "ar" else _build_lesson_prompt_fr(target_words_body)
        prompt_fallback += f"\n\nÉcris en {lang_name}. Quiz: options A/B/C/D, ✅, '{expl_label}:'."
        try:
            lesson_md, fb_inp, fb_out = await asyncio.to_thread(
                _groq_chat, client, model=model, system=prompt_fallback,
                user=f"Matière : {job.subject or 'General'}\n\nTRANSCRIPT :\n{transcript}",
                max_tokens=max_tokens, temperature=0.5,
            )
        except Exception as exc:
            return None, f"LLM fallback : {type(exc).__name__} — {str(exc)[:160]}"
        inp = p_inp + fb_inp
        out = p_out + fb_out
    else:
        # Génération par section
        n_sections = len(sections)
        target_per_section = max(400, target_words_body // max(1, n_sections))
        body_parts: list[str] = []
        cumul_inp = p_inp
        cumul_out = p_out

        for idx, sec in enumerate(sections):
            if not isinstance(sec, dict):
                continue
            title = str(sec.get("title", f"Section {idx+1}")).strip() or f"Section {idx+1}"
            points = sec.get("points") or []
            if not isinstance(points, list):
                points = []
            points = [str(p).strip() for p in points if str(p).strip()]
            if target_lang == "ar":
                sec_system = _build_section_dev_prompt_ar(title, points, target_per_section)
            else:
                sec_system = _build_section_dev_prompt_fr(title, points, target_per_section)
            sec_user = f"TRANSCRIPT ORIGINAL (pour contexte) :\n{transcript}\n\nDéveloppe maintenant la section *{title}* en t'appuyant sur ce transcript."
            try:
                sec_md, s_inp, s_out = await asyncio.to_thread(
                    _groq_chat,
                    client,
                    model=model,
                    system=sec_system,
                    user=sec_user,
                    max_tokens=2400,
                    temperature=0.5,
                )
                body_parts.append(sec_md.strip())
                cumul_inp += s_inp
                cumul_out += s_out
                logger.info(
                    "Section %d/%d '%s' : %d mots, in=%d out=%d",
                    idx + 1, n_sections, title[:40], len(sec_md.split()), s_inp, s_out,
                )
            except Exception:
                logger.exception("Section %d dev failed — on continue avec les autres", idx + 1)

        # Pass annexes : intro + glossaire + tableau + cheat sheet + quiz + flashcards + conclusion + plus-loin
        if target_lang == "ar":
            annexes_system = _build_annexes_prompt_ar(glossary_terms or [], expl_label)
        else:
            annexes_system = _build_annexes_prompt_fr(glossary_terms or [], expl_label)
        annexes_user = f"TRANSCRIPT ORIGINAL :\n{transcript}\n\nProduis les annexes."
        try:
            annexes_md, a_inp, a_out = await asyncio.to_thread(
                _groq_chat, client, model=model, system=annexes_system,
                user=annexes_user, max_tokens=4500, temperature=0.5,
            )
            cumul_inp += a_inp
            cumul_out += a_out
        except Exception:
            logger.exception("Annexes pass failed public_id=%s", job.public_id)
            annexes_md = ""

        # === Filtrage tautologies + retry si trop maigre ===
        if annexes_md:
            original_annexes = annexes_md
            cleaned_md, dropped, kept_q = _filter_tautological_quiz(annexes_md)
            if dropped > 0:
                logger.info("Quiz : %d question(s) tautologique(s) — %d restantes", dropped, kept_q)

            # Retry si beaucoup de questions virées (cible : 3+ questions valides).
            if kept_q < 3 and dropped > 0:
                logger.info("Quiz trop maigre après filtrage — retry avec prompt strict")
                retry_system = (
                    _build_quiz_retry_prompt_ar(expl_label) if target_lang == "ar"
                    else _build_quiz_retry_prompt_fr(expl_label)
                )
                try:
                    new_quiz_md, q_inp, q_out = await asyncio.to_thread(
                        _groq_chat, client, model=model, system=retry_system,
                        user=f"TRANSCRIPT :\n{transcript}\n\nProduis les 5 QCM.",
                        max_tokens=1800, temperature=0.4,
                    )
                    cumul_inp += q_inp
                    cumul_out += q_out
                    _, retry_dropped, retry_kept = _filter_tautological_quiz(new_quiz_md)
                    if retry_kept >= 2:
                        # Le retry a au moins 2 questions valides → on l'utilise
                        annexes_md = _replace_quiz_section(original_annexes, new_quiz_md)
                        logger.info("Quiz remplacé par retry (%d Q valides)", retry_kept)
                    else:
                        # Retry encore mauvais : on conserve l'ORIGINAL non filtré (au moins
                        # il y a un quiz, même imparfait, pour faire marcher /quiz et avoir
                        # une section visible dans le PDF).
                        logger.warning(
                            "Quiz retry encore mauvais (%d Q) — on garde l'original non filtré",
                            retry_kept,
                        )
                        annexes_md = original_annexes
                except Exception:
                    logger.exception("Quiz retry a échoué — on garde l'original non filtré")
                    annexes_md = original_annexes
            else:
                # Filtrage modéré → on garde la version nettoyée
                annexes_md = cleaned_md

        # === Assemblage final : intro AVANT corps, reste APRÈS corps ===
        body_md = "\n\n".join(body_parts).strip()
        # Strip les sous-sections '### Définition de X' redondantes (LLM ignore l'interdiction)
        body_md = _strip_redundant_definition_subsections(body_md)
        intro_block, rest_block = _split_annexes_intro_vs_rest(annexes_md or "")
        parts: list[str] = []
        if intro_block:
            parts.append(intro_block)
        if body_md:
            parts.append(body_md)
        if rest_block:
            parts.append(rest_block)
        lesson_md = "\n\n".join(parts).strip()

        inp = cumul_inp
        out = cumul_out
        logger.info(
            "Lesson section-by-section : %d sections, body=%d mots, intro=%d mots, rest=%d mots, total in=%d out=%d",
            n_sections, len(body_md.split()), len(intro_block.split()), len(rest_block.split()), inp, out,
        )

    # Débit wallet pour la génération (cohérent avec /api/generate).
    if job.user_id is not None:
        db = SessionLocal()
        try:
            user = db.get(User, job.user_id)
            if user is not None:
                _usd, mru = groq_billed(inp, out)
                charge_units = billed_mru_to_wallet_units_debit(mru)
                debit_credits(db, user, charge_units)
        except Exception:
            logger.exception("Débit wallet génération WhatsApp échoué public_id=%s", job.public_id)
        finally:
            db.close()

    if not lesson_md or not lesson_md.strip():
        return None, "Le LLM a renvoyé une fiche vide (réessaie avec /refaire)."

    return lesson_md, None


def _guess_user_locale(db: Session, user: User) -> Optional[str]:
    """Préfère ``user.whatsapp_language`` si posé, sinon dernière ``ui_locale`` connue."""
    explicit = getattr(user, "whatsapp_language", None)
    if explicit:
        return lang_for(explicit)
    last = db.execute(
        select(TranscriptionJob)
        .where(TranscriptionJob.user_id == user.id)
        .order_by(TranscriptionJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last and last.ui_locale:
        return lang_for(last.ui_locale)
    return None


async def _handle_langue_command(db: Session, user: User, phone: str, ui_loc: str, cmd: str) -> None:
    parts = cmd.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    if not arg:
        current = "Français" if ui_loc.startswith("fr") else "العربية"
        await _safe_send(phone, t("langue_current", ui_loc, current=current))
        return
    if arg.startswith("fr"):
        user.whatsapp_language = "fr"
        db.commit()
        await _safe_send(phone, t("langue_set_ok", "fr", lang="Français"))
        return
    if arg.startswith("ar"):
        user.whatsapp_language = "ar"
        db.commit()
        await _safe_send(phone, t("langue_set_ok", "ar", lang="العربية"))
        return
    await _safe_send(phone, t("langue_unknown", ui_loc))


async def _handle_matiere_command(db: Session, user: User, phone: str, ui_loc: str, cmd: str) -> None:
    parts = cmd.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        current = getattr(user, "whatsapp_subject", None) or ("auto" if user else "auto")
        await _safe_send(phone, t("matiere_current", ui_loc, current=current))
        return
    if arg.lower() in ("auto", "automatique", "تلقائي"):
        user.whatsapp_subject = None
        db.commit()
        await _safe_send(phone, t("matiere_auto_ok", ui_loc))
        return
    subject = arg[:128]
    user.whatsapp_subject = subject
    db.commit()
    await _safe_send(phone, t("matiere_set_ok", ui_loc, subject=subject))
    await _maybe_hint(phone, "hint_after_matiere_set", ui_loc)


async def _handle_partage_command(user: User, phone: str, ui_loc: str) -> None:
    """Active le partage public du dernier job. Génère la fiche si absente (facturée)."""
    lesson_md = await _ensure_lesson_or_generate(user, phone, ui_loc)
    if lesson_md is None:
        await _safe_send(phone, t("share_no_lesson", ui_loc))
        return

    db = SessionLocal()
    try:
        job = db.execute(
            select(TranscriptionJob)
            .where(TranscriptionJob.user_id == user.id)
            .where(TranscriptionJob.status == "done")
            .order_by(TranscriptionJob.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if job is None:
            await _safe_send(phone, t("share_no_lesson", ui_loc))
            return
        public_id = job.public_id
    finally:
        db.close()

    share_url, share_billed = _ensure_share_url(public_id)
    if not share_url:
        topup = wa_config.topup_url()
        if topup:
            await _safe_send(phone, t("wallet_blocked", ui_loc, topup_url=topup))
        else:
            await _safe_send(phone, t("wallet_blocked_no_url", ui_loc))
        return
    await _safe_send(phone, t("share_link", ui_loc, url=share_url))
    if share_billed > 0:
        await _safe_send(phone, t("share_billed", ui_loc, mru=f"{share_billed:.2f}"))


async def _handle_refaire_command(user: User, phone: str, ui_loc: str) -> None:
    """Force la re-génération LLM de la fiche + nouveau PDF (transcription non re-facturée)."""
    await _safe_send(phone, t("refaire_started", ui_loc))
    await _handle_pdf_command(user, phone, ui_loc, force_regen=True)


async def _handle_pdf_command(user: User, phone: str, ui_loc: str, *, force_regen: bool = False) -> None:
    """Construit + envoie le PDF du dernier job 'done'.

    - Si pas de ``lesson_markdown`` en base → génère via LLM (facturé) puis build PDF.
    - Si ``lesson_markdown`` existe et ``force_regen=False`` → rebâtit juste le PDF (zéro coût LLM).
    - Si ``force_regen=True`` (commande /refaire) → re-génère LLM systématiquement.
    """
    db = SessionLocal()
    try:
        job = db.execute(
            select(TranscriptionJob)
            .where(TranscriptionJob.user_id == user.id)
            .where(TranscriptionJob.status == "done")
            .order_by(TranscriptionJob.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if job is None:
            await _safe_send(phone, t("pdf_no_transcript", ui_loc))
            return
        job_id = job.id
        public_id = job.public_id
        subject = job.subject or "General"
        existing_md = job.lesson_markdown
    finally:
        db.close()

    if existing_md and not force_regen:
        await _safe_send(phone, t("pdf_lesson_cached", ui_loc))
        lesson_md = existing_md
    else:
        await _safe_send(phone, t("pdf_generating", ui_loc))
        db2 = SessionLocal()
        try:
            j = db2.get(TranscriptionJob, job_id)
            if j is None:
                return
            lesson_md, build_err = await _build_lesson_for_job(j)
        finally:
            db2.close()
        if not lesson_md or not lesson_md.strip():
            reason = build_err or "Erreur inconnue lors de la génération."
            await _safe_send(phone, t("generate_failed_with_reason", ui_loc, reason=reason))
            return
        db3 = SessionLocal()
        try:
            j = db3.get(TranscriptionJob, job_id)
            if j is not None:
                j.lesson_markdown = lesson_md
                db3.add(j)
                db3.commit()
        finally:
            db3.close()

    await _send_lesson_pdf_and_share(phone, ui_loc, public_id, subject, lesson_md)
    await _maybe_hint(phone, "hint_after_pdf", ui_loc)


async def _ensure_lesson_or_generate(user: User, phone: str, ui_loc: str) -> Optional[str]:
    """Renvoie ``lesson_markdown`` du dernier job, en la générant (avec débit LLM) si absente.

    Renvoie ``None`` si pas de job de transcription, ou en cas d'échec de génération.
    """
    db = SessionLocal()
    try:
        job = db.execute(
            select(TranscriptionJob)
            .where(TranscriptionJob.user_id == user.id)
            .where(TranscriptionJob.status == "done")
            .order_by(TranscriptionJob.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if job is None:
            return None
        if job.lesson_markdown:
            return job.lesson_markdown
        job_id = job.id
    finally:
        db.close()

    await _safe_send(phone, t("quiz_generating_lesson", ui_loc))
    db2 = SessionLocal()
    try:
        j = db2.get(TranscriptionJob, job_id)
        if j is None:
            return None
        lesson_md, build_err = await _build_lesson_for_job(j)
    finally:
        db2.close()
    if not lesson_md or not lesson_md.strip():
        reason = build_err or "Erreur inconnue lors de la génération."
        await _safe_send(phone, t("generate_failed_with_reason", ui_loc, reason=reason))
        return None
    db3 = SessionLocal()
    try:
        j = db3.get(TranscriptionJob, job_id)
        if j is not None:
            j.lesson_markdown = lesson_md
            db3.add(j)
            db3.commit()
    finally:
        db3.close()
    return lesson_md


async def _handle_quiz_command(user: User, phone: str, ui_loc: str) -> None:
    # Récupère le dernier job + sa fiche (la génère si absente, avec débit LLM).
    lesson_md = await _ensure_lesson_or_generate(user, phone, ui_loc)
    if lesson_md is None:
        await _safe_send(phone, t("quiz_none", ui_loc))
        return

    db = SessionLocal()
    try:
        job = db.execute(
            select(TranscriptionJob)
            .where(TranscriptionJob.user_id == user.id)
            .where(TranscriptionJob.status == "done")
            .order_by(TranscriptionJob.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        job_public_id = job.public_id if job else None
    finally:
        db.close()

    questions = _extract_quiz(lesson_md)
    if not questions:
        await _safe_send(phone, t("quiz_none", ui_loc))
        return

    # Débit symbolique pour démarrer une session quiz.
    from credits_wallet import debit_credits
    from pricing import (
        billed_mru_to_wallet_units_debit,
        WHATSAPP_QUIZ_BILLED_MRU,
    )

    quiz_units = billed_mru_to_wallet_units_debit(WHATSAPP_QUIZ_BILLED_MRU)
    if quiz_units > 0:
        db_q = SessionLocal()
        try:
            u = db_q.get(User, user.id)
            if u is None:
                return
            if int(u.credit_balance or 0) < quiz_units:
                topup = wa_config.topup_url()
                if topup:
                    await _safe_send(phone, t("wallet_blocked", ui_loc, topup_url=topup))
                else:
                    await _safe_send(phone, t("wallet_blocked_no_url", ui_loc))
                return
            try:
                debit_credits(db_q, u, quiz_units)
            except Exception:
                logger.exception("Débit quiz échoué phone=%s", phone)
                return
        finally:
            db_q.close()

    _quiz_sessions[phone] = {
        "questions": questions,
        "idx": 0,
        "score": 0,
        "ui_loc": ui_loc,
        "job_public_id": job_public_id,
    }
    await _safe_send(phone, t("quiz_intro", ui_loc, n=len(questions)))
    await _send_quiz_question(phone)


async def _send_quiz_question(phone: str) -> None:
    sess = _quiz_sessions.get(phone)
    if not sess:
        return
    questions = sess["questions"]
    idx = sess["idx"]
    if idx >= len(questions):
        ui_loc = sess["ui_loc"]
        score = sess["score"]
        total = len(questions)
        _quiz_sessions.pop(phone, None)
        await _safe_send(phone, t("quiz_done", ui_loc, score=score, total=total))
        await _maybe_hint(phone, "hint_after_quiz_done", ui_loc)
        return

    q = questions[idx]
    ui_loc = sess["ui_loc"]
    header_line = t("quiz_question", ui_loc, idx=idx + 1, total=len(questions), question=q["question"])
    options_text = "\n".join(f"*{let})* {txt}" for let, txt in q["options"])
    full_text = f"{header_line}\n\n{options_text}"

    # 1) Envoi de la question + options en clair (pas de troncature WhatsApp).
    await _safe_send(phone, full_text)

    # 2) Sélecteur interactif : titre court = lettre, description = extrait (72 chars max côté Meta).
    rows = [
        (f"quiz:{idx}:{letter}", f"{letter})", txt)
        for letter, txt in q["options"]
    ]
    selector_prompt = t("quiz_select_prompt", ui_loc)
    try:
        await wa_client.send_interactive_list(
            phone,
            body=selector_prompt,
            button_label=t("quiz_button", ui_loc),
            rows=rows,
            section_title=t("quiz_button", ui_loc),
        )
    except Exception:
        logger.exception("send_interactive_list quiz a échoué — fallback texte seul (déjà envoyé ci-dessus).")


async def _handle_quiz_reply(phone: str, reply_id: str) -> None:
    """Handle interactive list reply with id 'quiz:<idx>:<letter>'."""
    sess = _quiz_sessions.get(phone)
    if not sess:
        return
    parts = reply_id.split(":")
    if len(parts) != 3 or parts[0] != "quiz":
        return
    try:
        idx = int(parts[1])
    except ValueError:
        return
    letter = parts[2].upper()
    if idx != sess["idx"]:
        return  # réponse à une question périmée

    q = sess["questions"][idx]
    ui_loc = sess["ui_loc"]
    correct = q["correct"]
    explanation = q["explanation"] or ""

    if letter == correct:
        sess["score"] += 1
        await _safe_send(phone, t("quiz_correct", ui_loc, explanation=explanation))
    else:
        await _safe_send(phone, t("quiz_wrong", ui_loc, correct=correct, explanation=explanation))

    sess["idx"] += 1
    await _send_quiz_question(phone)


async def _handle_texte_command(user: User, phone: str, ui_loc: str) -> None:
    """Envoie la transcription brute du dernier job sous forme de fichier .txt."""
    import json as _json

    db = SessionLocal()
    try:
        job = db.execute(
            select(TranscriptionJob)
            .where(TranscriptionJob.user_id == user.id)
            .where(TranscriptionJob.status == "done")
            .order_by(TranscriptionJob.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        result_json = job.result_json if job else None
        public_id = job.public_id if job else None
        subject = (job.subject or "cours") if job else "cours"
    finally:
        db.close()

    if not result_json:
        await _safe_send(phone, t("texte_no_job", ui_loc))
        return

    try:
        payload = _json.loads(result_json)
    except Exception:
        payload = {}
    transcript = (payload.get("transcript") or payload.get("timestamped_transcript") or "").strip()
    if not transcript:
        await _safe_send(phone, t("texte_no_job", ui_loc))
        return

    # Écrit le .txt à côté du job.
    jdir = _DATA / "jobs" / public_id
    jdir.mkdir(parents=True, exist_ok=True)
    txt_path = jdir / "transcript.txt"
    txt_path.write_text(transcript, encoding="utf-8")

    try:
        media_id = await wa_client.upload_document(txt_path, mime_type="text/plain")
        filename = f"LecturAI_{subject.replace(' ', '_')[:40]}_transcription.txt"
        await wa_client.send_document(
            phone, media_id, filename=filename, caption=t("texte_caption", ui_loc)
        )
        await _safe_send(phone, t("texte_sent", ui_loc))
    except Exception as exc:
        logger.exception("Envoi .txt WhatsApp échoué public_id=%s", public_id)
        reason = f"Meta API : {type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(phone, t("send_pdf_failed_with_reason", ui_loc, reason=reason))


async def _send_lesson_pdf_and_share(
    phone: str, ui_loc: str, public_id: str, subject: str, lesson_md: str
) -> None:
    """Construit + envoie le PDF, puis un lien web partageable et propose un quiz si dispo.

    Facturation : débit symbolique aligné sur le web (``export_job_billed`` ≈ 0.026 MRU)
    appliqué après une livraison Meta réussie — cohérent avec le pricing /api/export/pdf.
    """
    jdir = _DATA / "jobs" / public_id
    jdir.mkdir(parents=True, exist_ok=True)

    # 1) Tentative PDF Elite (LaTeX, partagé avec le web) — qualité Harvard/Oxford.
    # 2) Fallback reportlab simple si LaTeX échoue (pdflatex absent, compile fail, etc.).
    pdf_bytes: Optional[bytes] = None
    page_count = 1
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    premium_attempted = False
    if api_key and os.getenv("WHATSAPP_PDF_ELITE_ENABLED", "true").lower() in ("1", "true", "yes", "on"):
        premium_attempted = True
        try:
            from pdf_premium import (
                generate_premium_pdf_bytes,
                PremiumPdfError,
                PremiumPdfUnavailable,
            )

            pdf_bytes, page_count, _latex = await asyncio.to_thread(
                generate_premium_pdf_bytes,
                lesson_markdown=lesson_md,
                subject=subject or "General",
                language=("ar" if ui_loc.startswith("ar") else "fr"),
                api_key=api_key,
            )
            logger.info("PDF Elite généré public_id=%s pages=%d bytes=%d", public_id, page_count, len(pdf_bytes))
        except PremiumPdfUnavailable as exc:
            logger.warning("PDF Elite indisponible (%s) — fallback reportlab simple", exc)
            pdf_bytes = None
        except PremiumPdfError as exc:
            logger.warning("PDF Elite a échoué (%s) — fallback reportlab simple", exc)
            pdf_bytes = None
        except Exception:
            logger.exception("PDF Elite exception inattendue — fallback reportlab simple")
            pdf_bytes = None

    if pdf_bytes is None:
        try:
            pdf_bytes, page_count = await asyncio.to_thread(
                build_lesson_pdf_bytes, lesson_md, subject or "General", ui_loc
            )
        except Exception as exc:
            logger.exception("build_lesson_pdf_bytes échoué public_id=%s", public_id)
            reason = f"PDF builder : {type(exc).__name__} — {str(exc)[:160]}"
            await _safe_send(phone, t("send_pdf_failed_with_reason", ui_loc, reason=reason))
            return

    pdf_path = jdir / "lesson.pdf"
    pdf_path.write_bytes(pdf_bytes)

    try:
        media_id = await wa_client.upload_document(pdf_path, mime_type="application/pdf")
        filename = f"LecturAI_{(subject or 'cours').replace(' ', '_')[:40]}.pdf"
        await wa_client.send_document(phone, media_id, filename=filename, caption=t("send_failed_caption", ui_loc))
    except Exception as exc:
        logger.exception("Envoi PDF WhatsApp échoué public_id=%s", public_id)
        reason = f"Meta API : {type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(phone, t("send_pdf_failed_with_reason", ui_loc, reason=reason))
        return

    # Facturation export PDF par page (env WHATSAPP_PDF_MRU_PER_PAGE, défaut 0.5 MRU/page).
    try:
        from credits_wallet import debit_credits
        from pricing import billed_mru_to_wallet_units_debit, whatsapp_pdf_pages_billed_mru

        mru_pdf = whatsapp_pdf_pages_billed_mru(page_count)
        units = billed_mru_to_wallet_units_debit(mru_pdf)
        db_dbg = SessionLocal()
        try:
            job = db_dbg.execute(
                select(TranscriptionJob).where(TranscriptionJob.public_id == public_id)
            ).scalar_one_or_none()
            if job and job.user_id and units > 0:
                u = db_dbg.get(User, job.user_id)
                if u is not None:
                    debit_credits(db_dbg, u, units)
        finally:
            db_dbg.close()

        # Transparence : notifier l'user du coût du PDF (par page).
        await _safe_send(
            phone, t("pdf_billed_pages", ui_loc, pages=page_count, mru=f"{mru_pdf:.2f}")
        )
    except Exception:
        logger.exception("Débit export PDF WhatsApp échoué public_id=%s", public_id)

    # Le lien web partageable et le bouton quiz ne sont plus envoyés automatiquement après le PDF :
    # l'user les déclenche via /partage et /quiz s'il le souhaite (facturation à la commande).


def _ensure_share_url(public_id: str) -> tuple[Optional[str], float]:
    """Active le partage public sur le job (si pas déjà). Retourne ``(url, mru_debite)``.

    Facturation : ``WHATSAPP_SHARE_BILLED_MRU`` est débitée **uniquement à la création** du
    token (1ʳᵉ fois). Les re-livraisons du même lien sont gratuites. Si le solde ne permet pas
    le débit, le partage n'est pas activé (retour ``(None, 0.0)``) — graceful.
    """
    from credits_wallet import debit_credits
    from pricing import (
        billed_mru_to_wallet_units_debit,
        WHATSAPP_SHARE_BILLED_MRU,
    )

    base = wa_config.public_app_base_url()
    if not base:
        return None, 0.0
    db = SessionLocal()
    try:
        job = db.execute(
            select(TranscriptionJob).where(TranscriptionJob.public_id == public_id)
        ).scalar_one_or_none()
        if job is None:
            return None, 0.0

        debited_mru = 0.0
        if not job.public_share_token:
            # 1ʳᵉ activation → débit.
            units = billed_mru_to_wallet_units_debit(WHATSAPP_SHARE_BILLED_MRU)
            if units > 0 and job.user_id:
                u = db.get(User, job.user_id)
                if u is None or int(u.credit_balance or 0) < units:
                    logger.info("Partage refusé (solde insuffisant) public_id=%s", public_id)
                    return None, 0.0
                try:
                    debit_credits(db, u, units)
                    debited_mru = float(WHATSAPP_SHARE_BILLED_MRU)
                except Exception:
                    logger.exception("Débit share token échoué public_id=%s", public_id)
                    return None, 0.0
            from datetime import datetime, timezone
            job.public_share_token = secrets.token_urlsafe(32)
            job.public_share_enabled_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
            db.refresh(job)
        return f"{base}/c/{job.public_share_token}", debited_mru
    except Exception:
        logger.exception("ensure_share_url failed public_id=%s", public_id)
        return None, 0.0
    finally:
        db.close()
