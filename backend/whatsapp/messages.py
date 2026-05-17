"""Templates de messages texte du bot, FR + AR.

Pourquoi pas du i18n complet : ce sont quelques chaînes courtes, dupliquées entre 2 langues —
overhead du i18next côté backend disproportionné. Tableau ``MESSAGES[lang][key]`` direct.
"""

from __future__ import annotations

from typing import Optional


def lang_for(ui_locale: Optional[str], default: str = "fr") -> str:
    if not ui_locale:
        return default
    return "ar" if str(ui_locale).strip().lower().startswith("ar") else "fr"


MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        "welcome_unknown": (
            "👋 *Bienvenue sur LecturAI !*\n\n"
            "Je transforme tes cours audio en *fiches PDF structurées* avec résumé, points-clés et quiz — en quelques minutes.\n\n"
            "🎁 *Pour commencer :*\n"
            "1. Crée ton compte (30s) : {signup_url}\n"
            "2. Inscris-toi avec ce numéro WhatsApp\n"
            "3. Renvoie-moi un audio de cours\n\n"
            "💡 Astuce : commence par un court extrait (1–2 min) pour découvrir le résultat."
        ),
        "welcome_unknown_no_url": (
            "👋 *Bienvenue sur LecturAI !*\n\n"
            "Je transforme tes cours audio en fiches PDF structurées (résumé + points-clés + quiz).\n\n"
            "Crée d'abord ton compte sur notre site avec ce numéro WhatsApp, puis renvoie-moi un audio."
        ),
        "ack_audio": "🎙️ Audio bien reçu. Je transcris et je génère ta fiche de cours — quelques minutes…",
        "help_text": (
            "📚 *LecturAI Bot*\n\n"
            "Envoie-moi un *audio* de ton cours. Quand la transcription est prête, tu choisis ce que tu veux générer (chaque action est facturée à la commande).\n\n"
            "*Après transcription* :\n"
            "• /pdf — fiche structurée PDF\n"
            "• /texte — transcription brute .txt (gratuit)\n"
            "• /quiz — quiz interactif\n"
            "• /partage — lien web public à partager\n"
            "• /refaire — re-générer la fiche\n\n"
            "*Réglages* :\n"
            "• /modele — modèle de transcription (turbo/equilibre/affine/excellence)\n"
            "• /matiere — matière du cours\n"
            "• /langue — langue (fr/ar)\n"
            "• /skip — sauter la question de matière après un audio\n\n"
            "*Compte* :\n"
            "• /solde — voir ton solde MRU\n"
            "• /aide — afficher ce message"
        ),
        "modele_list": (
            "🎚️ *Modèle de transcription*\n\n"
            "Actuel : *{current_label}* ({current_mru} MRU/h)\n\n"
            "Pour changer, envoie :\n"
            "• /modele turbo — Réponse express (≈ {p_turbo} MRU/h)\n"
            "• /modele equilibre — Profil équilibré (≈ {p_large} MRU/h)\n"
            "• /modele affine — Transcription affinée (≈ {p_4omini} MRU/h)\n"
            "• /modele excellence — Excellence audio (≈ {p_w1} MRU/h)"
        ),
        "modele_set_ok": "✅ Modèle de transcription mis à jour : *{label}* (≈ {mru} MRU/h).",
        "modele_unknown": (
            "❓ Modèle inconnu : *{alias}*.\n\n"
            "Options valides : turbo, equilibre, affine, excellence.\n"
            "Tape /modele pour voir le détail."
        ),
        "unsupported_type": "Désolé, ce type de message n'est pas pris en charge. Envoie-moi un audio de ton cours 🎙️",
        "wallet_blocked": (
            "🪫 Ton solde MRU est insuffisant ou expiré.\n\n"
            "Recharge ton portefeuille ici :\n{topup_url}"
        ),
        "wallet_blocked_no_url": (
            "🪫 Ton solde MRU est insuffisant ou expiré. Recharge ton portefeuille depuis l'app pour continuer."
        ),
        "balance_line": "💰 Solde actuel : *{mru} MRU*",
        "rate_limited": "⏳ Attends quelques secondes avant d'envoyer un nouveau message.",
        "transcribe_failed": "❌ Désolé, la transcription a échoué. Réessaie avec un autre audio.",
        "transcribe_failed_with_reason": (
            "❌ La transcription a échoué.\n"
            "*Cause :* {reason}\n\n"
            "Réessaie avec un autre audio, ou /modele excellence pour un audio bruité."
        ),
        "generate_failed": "❌ La génération du cours a échoué. Ta transcription est sauvegardée — réessaie depuis l'app.",
        "generate_failed_with_reason": (
            "❌ La génération de la fiche a échoué.\n"
            "*Cause :* {reason}\n\n"
            "Ta transcription est sauvegardée — tape /refaire pour réessayer."
        ),
        "download_failed_with_reason": (
            "❌ Téléchargement du média échoué.\n"
            "*Cause :* {reason}\n\n"
            "Renvoie l'audio."
        ),
        "send_pdf_failed_with_reason": (
            "❌ Envoi du PDF échoué.\n"
            "*Cause :* {reason}\n\n"
            "Tape /refaire pour relancer."
        ),
        "send_failed_caption": "📄 Voici ta fiche de cours. (PDF généré par LecturAI)",
        "media_too_large": "📦 L'audio est trop volumineux. Limite : 60 Mo. Coupe-le en morceaux et renvoie.",
        "progress_transcribing": "📝 Transcription en cours… (modèle : {model})",
        "progress_generating": "✨ Transcription terminée. Génération de la fiche…",
        "share_link": "🔗 Lecture web / partage : {url}",
        "matiere_current": (
            "📘 *Matière*\n\n"
            "Actuelle : *{current}*\n\n"
            "Pour changer, envoie : /matiere <nom>\n"
            "Exemples : /matiere Mathématiques, /matiere Histoire, /matiere auto\n"
            "(« auto » → détection à partir du contenu de chaque audio)"
        ),
        "matiere_set_ok": "✅ Matière définie : *{subject}*.",
        "matiere_auto_ok": "✅ Matière en mode *auto* (détection à partir du contenu).",
        "langue_current": (
            "🗣️ *Langue actuelle :* {current}\n\n"
            "Pour changer : /langue fr ou /langue ar"
        ),
        "langue_set_ok": "✅ Langue définie : *{lang}*.",
        "langue_unknown": "❓ Langue inconnue. Utilise : /langue fr ou /langue ar.",
        "refaire_no_job": "ℹ️ Pas encore de cours à re-générer. Envoie d'abord un audio.",
        "refaire_started": "♻️ Re-génération à partir de ta dernière transcription…",
        "quiz_intro": "🎯 *Quiz interactif* — {n} question(s). Réponds en cliquant sur une option.",
        "quiz_question": "Question {idx}/{total}\n\n{question}",
        "quiz_correct": "✅ Bonne réponse !\n\n{explanation}",
        "quiz_wrong": "❌ Mauvaise réponse. La bonne était *{correct}*.\n\n{explanation}",
        "quiz_done": "🏁 Quiz terminé : *{score}/{total}* bonnes réponses.",
        "quiz_none": "ℹ️ Pas de quiz dans ton dernier cours.",
        "quiz_button": "Choisir",
        "quiz_select_prompt": "👉 Choisis ta réponse :",
        # Astuces contextuelles — envoyées au plus 1 fois par session pour ne pas spammer.
        "hint_after_ack": "💡 Astuce : *_/matiere_* précise la matière, *_/langue_* la langue. /aide pour tout voir.",
        "hint_after_pdf": "💡 Pas satisfait ? *_/refaire_* relance la génération. Réviser ? *_/quiz_*. /aide pour la liste.",
        "hint_after_quiz_done": "💡 Tape *_/refaire_* pour une autre version de la fiche, ou envoie un nouveau cours.",
        "hint_after_modele_set": "💡 Le nouveau modèle s'appliquera à ton prochain audio.",
        "hint_after_matiere_set": "💡 Matière enregistrée. Elle servira pour les prochains cours.",
        "hint_after_transcribe_fail": "💡 Essaie *_/modele excellence_* pour un audio bruité, ou renvoie un extrait plus court.",
        "hint_balance_low": "💡 Solde bas. Recharge ici : {topup_url}",
        "hint_unknown_command": "💡 Tape *_/aide_* pour voir toutes les commandes disponibles.",
        "texte_no_job": "ℹ️ Pas de transcription disponible. Envoie d'abord un audio.",
        "texte_caption": "📝 Transcription brute de ton audio.",
        "texte_sent": "✅ Transcription envoyée en .txt.",
        "pdf_billed_pages": "📄 PDF de *{pages} page(s)* — *{mru} MRU* débitées.",
        "transcription_ready": "✅ *Transcription terminée* — matière : *{subject}*.",
        "menu_after_transcription": (
            "🎯 *Que veux-tu faire ?* (chaque action est facturée à la commande)\n\n"
            "• */pdf* — fiche structurée PDF (*~0.4 MRU LLM + 0.5 MRU/page*)\n"
            "• */texte* — transcription brute .txt (*gratuit*)\n"
            "• */quiz* — quiz interactif (*0.02 MRU*, génère la fiche si nécessaire)\n"
            "• */partage* — lien web public à partager (*0.5 MRU*, 1ʳᵉ fois)\n"
            "• */refaire* — re-générer la fiche après /pdf"
        ),
        "pdf_no_transcript": "ℹ️ Pas de transcription disponible. Envoie d'abord un audio.",
        "pdf_lesson_cached": "♻️ Fiche déjà en cache — je rebâtis juste le PDF (pas de coût LLM).",
        "pdf_generating": "✨ Génération de la fiche…",
        "share_no_lesson": "ℹ️ Aucune fiche disponible. Tape */pdf* d'abord (génère la fiche), puis /partage.",
        "quiz_generating_lesson": "✨ Génération de la fiche (nécessaire pour le quiz)…",
        "share_billed": "🔗 Activation du lien de partage : *{mru} MRU* débitées (1×, ré-utilisable gratuitement).",
        "ask_matiere": (
            "📘 *Quelle matière pour ce cours ?*\n\n"
            "Réponds avec la matière (ex: Mathématiques, Physique, Histoire),\n"
            "ou tape */skip* pour la détection automatique."
        ),
        "matiere_received": "✅ Matière : *{subject}*. Je lance la transcription…",
        "matiere_skipped": "✅ OK, je détecte la matière automatiquement. Je lance la transcription…",
        "matiere_skipped_auto": "⏱️ Pas de réponse — je détecte la matière automatiquement et lance la transcription.",
        "pending_expired": "⏳ Audio en attente expiré — renvoie-le si tu veux la fiche.",
        "pending_cancelled": "ℹ️ Audio en attente annulé. Renvoie l'audio pour redémarrer.",
    },
    "ar": {
        "welcome_unknown": (
            "👋 مرحبًا بك في بوت LecturAI!\n\n"
            "للاستخدام، أنشئ حسابك أولًا هنا:\n{signup_url}\n\n"
            "بعد التسجيل بهذا الرقم، أرسل لي ملفًا صوتيًا."
        ),
        "welcome_unknown_no_url": (
            "👋 مرحبًا بك في بوت LecturAI!\n\n"
            "للاستخدام، أنشئ حسابك أولًا على موقعنا، ثم أرسل لي ملفًا صوتيًا."
        ),
        "ack_audio": "🎙️ تم استلام الصوت. أقوم بالنسخ وإنشاء بطاقة الدرس — بضع دقائق…",
        "help_text": (
            "📚 *بوت LecturAI*\n\n"
            "أرسل لي *تسجيلًا صوتيًا* لدرسك. بعد اكتمال النسخ، تختار ما تريد إنشاءه (كل أمر يُحسب عند تنفيذه).\n\n"
            "*بعد النسخ*:\n"
            "• /pdf — بطاقة PDF منظمة\n"
            "• /texte — النص الخام .txt (مجاناً)\n"
            "• /quiz — اختبار تفاعلي\n"
            "• /partage — رابط ويب عام للمشاركة\n"
            "• /refaire — إعادة إنشاء البطاقة\n\n"
            "*الإعدادات*:\n"
            "• /modele — نموذج النسخ\n"
            "• /matiere — مادة الدرس\n"
            "• /langue — اللغة (fr/ar)\n"
            "• /skip — تخطّي سؤال المادة\n\n"
            "*الحساب*:\n"
            "• /solde — الرصيد\n"
            "• /aide — هذه الرسالة"
        ),
        "modele_list": (
            "🎚️ *نموذج النسخ*\n\n"
            "الحالي: *{current_label}* ({current_mru} أوقية/ساعة)\n\n"
            "للتغيير، أرسل:\n"
            "• /modele turbo — سريع التسليم (≈ {p_turbo} أوقية/ساعة)\n"
            "• /modele equilibre — متوازن الجودة (≈ {p_large} أوقية/ساعة)\n"
            "• /modele affine — نسخ دقيق (≈ {p_4omini} أوقية/ساعة)\n"
            "• /modele excellence — جودة فائقة (≈ {p_w1} أوقية/ساعة)"
        ),
        "modele_set_ok": "✅ تم تحديث نموذج النسخ: *{label}* (≈ {mru} أوقية/ساعة).",
        "modele_unknown": (
            "❓ نموذج غير معروف: *{alias}*.\n\n"
            "الخيارات: turbo, equilibre, affine, excellence.\n"
            "اكتب /modele لرؤية التفاصيل."
        ),
        "unsupported_type": "عذرًا، هذا النوع غير مدعوم. أرسل لي تسجيلًا صوتيًا لدرسك 🎙️",
        "wallet_blocked": (
            "🪫 رصيدك بالأوقية غير كافٍ أو منتهي الصلاحية.\n\n"
            "أعد شحن محفظتك هنا:\n{topup_url}"
        ),
        "wallet_blocked_no_url": (
            "🪫 رصيدك بالأوقية غير كافٍ. قم بإعادة الشحن من التطبيق للمتابعة."
        ),
        "balance_line": "💰 الرصيد الحالي: *{mru} أوقية*",
        "rate_limited": "⏳ انتظر بضع ثوانٍ قبل إرسال رسالة جديدة.",
        "transcribe_failed": "❌ عذرًا، فشل النسخ. حاول مع تسجيل آخر.",
        "transcribe_failed_with_reason": (
            "❌ فشل النسخ.\n"
            "*السبب:* {reason}\n\n"
            "حاول بتسجيل آخر، أو /modele excellence لصوت مشوّش."
        ),
        "generate_failed": "❌ فشل إنشاء الدرس. تم حفظ النسخ — أعد المحاولة من التطبيق.",
        "generate_failed_with_reason": (
            "❌ فشل إنشاء البطاقة.\n"
            "*السبب:* {reason}\n\n"
            "النسخ محفوظ — اكتب /refaire لإعادة المحاولة."
        ),
        "download_failed_with_reason": (
            "❌ فشل تحميل الملف الصوتي.\n"
            "*السبب:* {reason}\n\n"
            "أعد إرسال التسجيل."
        ),
        "send_pdf_failed_with_reason": (
            "❌ فشل إرسال ملف PDF.\n"
            "*السبب:* {reason}\n\n"
            "اكتب /refaire لإعادة المحاولة."
        ),
        "send_failed_caption": "📄 هذه بطاقة درسك. (PDF بواسطة LecturAI)",
        "media_too_large": "📦 الملف الصوتي كبير جدًا. الحد الأقصى: 60 ميغابايت. قسّم الملف وأعد الإرسال.",
        "progress_transcribing": "📝 جاري النسخ… (النموذج: {model})",
        "progress_generating": "✨ اكتمل النسخ. جاري إنشاء البطاقة…",
        "share_link": "🔗 قراءة / مشاركة على الويب: {url}",
        "matiere_current": (
            "📘 *المادة*\n\n"
            "الحالية: *{current}*\n\n"
            "للتغيير، أرسل: /matiere <اسم>\n"
            "أمثلة: /matiere رياضيات, /matiere تاريخ, /matiere auto\n"
            "(« auto » → الاكتشاف التلقائي من محتوى كل تسجيل)"
        ),
        "matiere_set_ok": "✅ تم تعيين المادة: *{subject}*.",
        "matiere_auto_ok": "✅ المادة في وضع *تلقائي*.",
        "langue_current": (
            "🗣️ *اللغة الحالية:* {current}\n\n"
            "للتغيير: /langue fr أو /langue ar"
        ),
        "langue_set_ok": "✅ تم تعيين اللغة: *{lang}*.",
        "langue_unknown": "❓ لغة غير معروفة. استخدم: /langue fr أو /langue ar.",
        "refaire_no_job": "ℹ️ لا يوجد درس لإعادة إنشائه. أرسل تسجيلًا أولًا.",
        "refaire_started": "♻️ جاري إعادة إنشاء الدرس من آخر نسخ…",
        "quiz_intro": "🎯 *اختبار تفاعلي* — {n} سؤال. أجب بالضغط على خيار.",
        "quiz_question": "السؤال {idx}/{total}\n\n{question}",
        "quiz_correct": "✅ إجابة صحيحة!\n\n{explanation}",
        "quiz_wrong": "❌ إجابة خاطئة. الصحيحة كانت *{correct}*.\n\n{explanation}",
        "quiz_done": "🏁 انتهى الاختبار: *{score}/{total}* إجابات صحيحة.",
        "quiz_none": "ℹ️ لا يوجد اختبار في آخر درس.",
        "quiz_button": "اختر",
        "quiz_select_prompt": "👉 اختر إجابتك:",
        "hint_after_ack": "💡 نصيحة: *_/matiere_* لتحديد المادة، *_/langue_* للغة. /aide لعرض الكل.",
        "hint_after_pdf": "💡 لست راضيًا؟ *_/refaire_* لإعادة الإنشاء. للمراجعة: *_/quiz_*. /aide لعرض الكل.",
        "hint_after_quiz_done": "💡 اكتب *_/refaire_* لنسخة أخرى من البطاقة، أو أرسل درسًا جديدًا.",
        "hint_after_modele_set": "💡 سيُطبق النموذج الجديد على تسجيلك التالي.",
        "hint_after_matiere_set": "💡 تم حفظ المادة. ستُستخدم في الدروس القادمة.",
        "hint_after_transcribe_fail": "💡 جرّب *_/modele excellence_* للصوت المشوّش، أو أرسل مقطعًا أقصر.",
        "hint_balance_low": "💡 الرصيد منخفض. أعد الشحن هنا: {topup_url}",
        "hint_unknown_command": "💡 اكتب *_/aide_* لعرض جميع الأوامر المتاحة.",
        "texte_no_job": "ℹ️ لا يوجد نسخ متاح. أرسل تسجيلًا أولًا.",
        "texte_caption": "📝 النص الخام لتسجيلك.",
        "texte_sent": "✅ تم إرسال النسخ بصيغة .txt.",
        "pdf_billed_pages": "📄 ملف PDF من *{pages} صفحة* — تم خصم *{mru} أوقية*.",
        "transcription_ready": "✅ *اكتمل النسخ* — المادة: *{subject}*.",
        "menu_after_transcription": (
            "🎯 *ماذا تريد؟* (كل أمر يُحسب عند تنفيذه)\n\n"
            "• */pdf* — بطاقة PDF منظمة (*~0.4 أوقية LLM + 0.5 أوقية/صفحة*)\n"
            "• */texte* — النص الخام .txt (*مجاناً*)\n"
            "• */quiz* — اختبار تفاعلي (*0.02 أوقية*، يولّد البطاقة عند الحاجة)\n"
            "• */partage* — رابط ويب عام للمشاركة (*0.5 أوقية*، أول مرة)\n"
            "• */refaire* — إعادة إنشاء البطاقة بعد /pdf"
        ),
        "pdf_no_transcript": "ℹ️ لا يوجد نسخ متاح. أرسل تسجيلًا أولًا.",
        "pdf_lesson_cached": "♻️ البطاقة محفوظة — أُعيد بناء PDF فقط (دون تكلفة LLM).",
        "pdf_generating": "✨ جاري إنشاء البطاقة…",
        "share_no_lesson": "ℹ️ لا توجد بطاقة. اكتب */pdf* أولًا، ثم /partage.",
        "quiz_generating_lesson": "✨ إنشاء البطاقة (لازم للاختبار)…",
        "share_billed": "🔗 تفعيل رابط المشاركة: تم خصم *{mru} أوقية* (مرة واحدة، يمكن إعادة استخدامه مجانًا).",
        "ask_matiere": (
            "📘 *ما هي مادة هذا الدرس؟*\n\n"
            "أجب باسم المادة (مثل: رياضيات، فيزياء، تاريخ)،\n"
            "أو اكتب */skip* للاكتشاف التلقائي."
        ),
        "matiere_received": "✅ المادة: *{subject}*. جاري النسخ…",
        "matiere_skipped": "✅ حسنًا، سأكتشف المادة تلقائيًا. جاري النسخ…",
        "matiere_skipped_auto": "⏱️ لا رد — سأكتشف المادة تلقائيًا وأبدأ النسخ.",
        "pending_expired": "⏳ الصوت المنتظر انتهت صلاحيته — أرسله مجددًا إن أردت البطاقة.",
        "pending_cancelled": "ℹ️ تم إلغاء الصوت المنتظر. أعد الإرسال للبدء من جديد.",
    },
}


def t(key: str, ui_locale: Optional[str] = None, **fmt: object) -> str:
    lang = lang_for(ui_locale)
    bag = MESSAGES.get(lang) or MESSAGES["fr"]
    raw = bag.get(key) or MESSAGES["fr"].get(key) or key
    try:
        return raw.format(**fmt) if fmt else raw
    except (KeyError, IndexError):
        return raw
