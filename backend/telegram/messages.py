"""Templates de messages texte spécifiques au bot Telegram.

Stratégie : on **réutilise** ``whatsapp.messages.MESSAGES`` (FR + AR) pour tout ce qui est
identique sémantiquement (modèles, soldes, erreurs…). On surcharge ici uniquement les
clés où le texte WA mentionne "WhatsApp" / "numéro", et on ajoute les clés exclusives
à Telegram (``/lier`` : liaison de compte).

Fonction publique : ``t(key, ui_locale, **fmt)`` — même API que ``whatsapp.messages.t``.
On délègue le fallback au module WA si la clé n'est pas surchargée ici.
"""

from __future__ import annotations

from typing import Optional

from whatsapp.messages import MESSAGES as WA_MESSAGES, lang_for


# Surcharges Telegram. Toute clé absente de ce dict tombe automatiquement sur la version WhatsApp.
_TG_OVERRIDES: dict[str, dict[str, str]] = {
    "fr": {
        "welcome_unknown": (
            "👋 *Bienvenue sur LecturAI !*\n\n"
            "Je transforme tes cours audio en *fiches PDF structurées* avec résumé et points-clés.\n\n"
            "🔗 *Pour utiliser ce bot* :\n"
            "1. Connecte-toi à ton compte LecturAI : {signup_url}\n"
            "2. Clique sur le bouton *« Lier mon Telegram »* dans l'app\n"
            "3. Tu seras renvoyé(e) ici et le compte sera lié automatiquement"
        ),
        "welcome_unknown_no_url": (
            "👋 *Bienvenue sur LecturAI !*\n\n"
            "Pour utiliser ce bot, connecte-toi à ton compte LecturAI dans l'app et clique sur "
            "*« Lier mon Telegram »*."
        ),
        "help_text": (
            "📚 *LecturAI Bot Telegram*\n\n"
            "Envoie-moi un *audio* (vocal ou fichier) de ton cours. Après transcription, tu choisis ce que tu veux générer.\n\n"
            "*Compte* :\n"
            "• /lier — lien pour lier ton compte (via l'app web)\n"
            "• /delier — délier ce chat de ton compte\n"
            "• /solde — voir ton solde MRU\n\n"
            "*Après transcription* :\n"
            "• /pdf — fiche structurée PDF\n"
            "• /texte — transcription brute .txt (gratuit)\n"
            "• /quiz — quiz interactif\n"
            "• /partage — lien web public à partager\n"
            "• /refaire — re-générer la fiche\n"
            "• /confiance — score de qualité ASR + passages problématiques\n\n"
            "*Réglages* :\n"
            "• /modele — modèle de transcription\n"
            "• /matiere — matière du cours\n"
            "• /langue — langue (fr/ar)\n"
            "• /aide — ce message"
        ),
        # === Liaison de compte (via deep link depuis l'app web) ===
        "lier_usage": (
            "🔗 *Lier ton compte LecturAI*\n\n"
            "Pour des raisons de sécurité, la liaison se fait depuis l'app :\n"
            "1. Connecte-toi sur {signup_url}\n"
            "2. Clique sur *« Lier mon Telegram »* dans ton profil\n"
            "3. Tu seras renvoyé(e) ici automatiquement\n\n"
            "_(Le lien direct par numéro a été retiré pour éviter qu'un tiers ne lie ton compte à son chat.)_"
        ),
        "lier_usage_no_url": (
            "🔗 *Lier ton compte LecturAI*\n\n"
            "Connecte-toi à l'app et clique sur *« Lier mon Telegram »* — tu seras renvoyé(e) ici automatiquement."
        ),
        "lier_token_invalid": (
            "❌ Lien invalide ou expiré.\n\n"
            "Retourne sur l'app web, reclique sur *« Lier mon Telegram »* et utilise le nouveau lien (valide 5 min)."
        ),
        "lier_already_linked_other": (
            "⚠️ Ce compte est déjà lié à un *autre* chat Telegram.\n\n"
            "Utilise */delier* depuis l'app web sur le compte concerné, puis recommence."
        ),
        "lier_chat_already_linked": (
            "ℹ️ Ce chat Telegram est déjà lié à ton compte. Tape /solde pour vérifier."
        ),
        "lier_ok": "✅ Compte lié avec succès. Envoie-moi un audio de cours pour commencer !",
        "delier_ok": "✅ Chat délié. Tape /lier pour relier un compte.",
        "delier_not_linked": "ℹ️ Aucun compte n'est lié à ce chat.",
        # === Blocage des commandes payantes si confiance Whisper trop basse ===
        "low_confidence_block": (
            "🚫 *Fiche bloquée — qualité audio insuffisante*\n\n"
            "Confiance Whisper : *{score:.0f}%* (seuil minimum : {threshold:.0f}%).\n\n"
            "Sur cet audio, Whisper hallucine ({ratio:.0f}% de la durée). Générer une fiche maintenant "
            "produirait du contenu inventé — ce n'est pas honnête vis-à-vis de toi.\n\n"
            "*Que faire :*\n"
            "• 🎙️ Renvoie l'*audio original* (pas un forward de forward) — chaque ré-encodage dégrade.\n"
            "• 🔊 Augmente le volume du fichier dans un éditeur audio avant de l'envoyer.\n"
            "• 🌐 Utilise l'*app web* avec un meilleur audio.\n"
            "• 📝 */texte* reste disponible (gratuit) pour récupérer le transcript brut tel quel.\n"
            "• 🔍 */confiance* pour voir les passages problématiques."
        ),
        "low_confidence_block_unknown": (
            "🚫 *Fiche bloquée — pas de données de confiance*\n\n"
            "Impossible de mesurer la qualité de cet audio (modèle utilisé ne renvoie pas les métriques). "
            "Essaie */modele turbo* ou */modele excellence* puis renvoie l'audio."
        ),
        # === Pré-vérification audio : audio rejeté avant transcription (économie de crédits) ===
        "audio_too_quiet_block": (
            "📉 *Audio rejeté — qualité insuffisante détectée*\n\n"
            "{reason}\n\n"
            "À ce niveau de volume, Whisper va halluciner (texte inventé). "
            "Aucun crédit n'a été débité.\n\n"
            "*Que faire :*\n"
            "• 🎙️ Renvoie l'audio *original* (un forward de forward perd en qualité à chaque étape).\n"
            "• 🔊 *Amplifie* le fichier côté Mac avant l'envoi :\n"
            "    – QuickTime : ouvre → Édition → Augmenter Volume\n"
            "    – Audacity : Effet → Normaliser → -1 dB\n"
            "    – ffmpeg : `ffmpeg -i in.m4a -filter:a 'volume=15dB' out.m4a`\n"
            "• 🌐 Ou utilise l'*app web* avec un audio de meilleure qualité."
        ),
        # Nouveau : blocage basé sur ratio de durée utilisable plutôt que score brut
        "low_usable_ratio_block": (
            "🚫 *Fiche bloquée — trop de passages inaudibles*\n\n"
            "Seulement *{usable_pct:.0f}%* du cours est exploitable "
            "({inaudible_min} min {inaudible_sec:02d}s inaudibles sur {total_min} min total).\n\n"
            "Seuil minimum : *{threshold_pct:.0f}%* de durée exploitable pour générer une fiche fiable.\n\n"
            "*Que faire :*\n"
            "• 📝 */texte* — récupère le transcript brut avec les marqueurs `[inaudible MM:SS]` aux trous\n"
            "• 🔍 */confiance* — voir le détail des passages problématiques\n"
            "• 🎙️ Renvoie un *autre enregistrement* du même cours (qualité meilleure)"
        ),
        # Heartbeat pendant l'attente de la transcription. Affiché toutes les ~60s avec la phase courante.
        "progress_heartbeat": "⏳ Toujours en cours — *{phase}* ({pct}%)",
        "progress_heartbeat_nopct": "⏳ Toujours en cours — *{phase}*",
        # Étiquettes des phases (techniques côté ASR → libellés user-friendly)
        "phase_received": "fichier reçu",
        "phase_preprocessing": "préparation de l'audio",
        "phase_whisper_chunk": "découpage en segments",
        "phase_whisper": "transcription par Whisper",
        "phase_whisper_complete": "transcription terminée, nettoyage",
        "phase_post_process": "post-traitement (ponctuation, structure)",
        "phase_running": "transcription en cours",
        "phase_accepted": "en file d'attente",
        "unsupported_type": "Désolé, ce type de message n'est pas pris en charge. Envoie-moi un *audio* ou un *vocal* de ton cours 🎙️",
        # === Limite Telegram 20 MB — surcharge pour donner des alternatives concrètes ===
        # (Le message WhatsApp parle de "60 Mo" — ici on adapte au vrai plafond du Bot API.)
        "media_too_large": (
            "📦 *Fichier trop volumineux pour Telegram* (limite : 20 Mo côté bot).\n\n"
            "Solutions :\n"
            "• ✂️ *Découpe l'audio* en plusieurs morceaux (ex: 20-30 min chacun) et envoie-les un par un.\n"
            "• 🎙️ Ou enregistre-le comme *message vocal* dans Telegram (icône micro) — l'enregistrement direct compresse en OPUS et tient souvent sous la limite, même pour 1h+ de cours.\n"
            "• 🌐 Ou utilise l'*app web* LecturAI (limite plus élevée) : {signup_url}"
        ),
        "media_too_large_no_url": (
            "📦 *Fichier trop volumineux pour Telegram* (limite : 20 Mo côté bot).\n\n"
            "Solutions :\n"
            "• ✂️ Découpe l'audio en plusieurs morceaux (20-30 min chacun) et envoie-les un par un.\n"
            "• 🎙️ Enregistre-le comme *message vocal* (icône micro) — la compression OPUS tient souvent sous la limite, même pour 1h+ de cours."
        ),
    },
    "ar": {
        "welcome_unknown": (
            "👋 *مرحبًا بك في LecturAI!*\n\n"
            "أحوّل تسجيلاتك الصوتية إلى *بطاقات PDF منظمة*.\n\n"
            "🔗 *لديك حساب LecturAI (عبر واتساب أو الويب)؟*\n"
            "اربط حسابك بـ: */lier +22241234567* (رقم واتساب المسجّل به).\n\n"
            "📝 *لا تملك حسابًا بعد؟*\n"
            "أنشئه أولًا هنا: {signup_url}\n"
            "ثم عُد واكتب /lier."
        ),
        "welcome_unknown_no_url": (
            "👋 *مرحبًا بك في LecturAI!*\n\n"
            "لاستخدام هذا البوت، اربط حسابك أولًا برقم واتساب المسجّل به:\n"
            "*/lier +22241234567*"
        ),
        "help_text": (
            "📚 *بوت LecturAI تلغرام*\n\n"
            "أرسل لي *تسجيلًا صوتيًا* (رسالة صوتية أو ملفًا) لدرسك.\n\n"
            "*الحساب*:\n"
            "• /lier <رقم واتساب> — ربط هذه المحادثة بحسابك\n"
            "• /solde — الرصيد\n\n"
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
            "• /aide — هذه الرسالة"
        ),
        "lier_usage": (
            "🔗 *ربط حسابك في LecturAI*\n\n"
            "لأسباب أمنية، يتم الربط من التطبيق:\n"
            "1. سجّل الدخول على {signup_url}\n"
            "2. اضغط *« ربط تلغرام »* في ملفك الشخصي\n"
            "3. ستتم إعادتك إلى هنا تلقائيًا"
        ),
        "lier_usage_no_url": (
            "🔗 *ربط حسابك في LecturAI*\n\n"
            "سجّل الدخول إلى التطبيق واضغط *« ربط تلغرام »* — ستتم إعادتك إلى هنا تلقائيًا."
        ),
        "lier_token_invalid": (
            "❌ الرابط غير صالح أو منتهي.\n\n"
            "عُد إلى التطبيق وأعد الضغط على *« ربط تلغرام »* (صالح 5 دقائق)."
        ),
        "lier_already_linked_other": (
            "⚠️ هذا الحساب مرتبط بالفعل بمحادثة تلغرام *أخرى*. استخدم */delier* من الحساب المعني."
        ),
        "lier_chat_already_linked": "ℹ️ هذه المحادثة مرتبطة بالفعل بحسابك. اكتب /solde للتحقق.",
        "lier_ok": "✅ تم ربط الحساب بنجاح. أرسل تسجيلًا صوتيًا للبدء!",
        "delier_ok": "✅ تم فصل المحادثة. اكتب /lier لربط حساب آخر.",
        "delier_not_linked": "ℹ️ لا يوجد حساب مرتبط بهذه المحادثة.",
        "low_confidence_block": (
            "🚫 *البطاقة محظورة — جودة الصوت غير كافية*\n\n"
            "ثقة Whisper: *{score:.0f}%* (الحد الأدنى: {threshold:.0f}%).\n\n"
            "Whisper يهلوس في {ratio:.0f}% من المدة. إنشاء البطاقة الآن سيُنتج محتوى مُختلقًا.\n\n"
            "*الحلول:*\n"
            "• 🎙️ أعد إرسال الصوت *الأصلي* (دون forward متعدد).\n"
            "• 🔊 ارفع مستوى الصوت قبل الإرسال.\n"
            "• 🌐 استخدم *تطبيق الويب* بصوت أفضل.\n"
            "• 📝 */texte* لا يزال متاحًا (مجاناً) لاستلام النص الخام.\n"
            "• 🔍 */confiance* لرؤية المقاطع المشكلة."
        ),
        "low_confidence_block_unknown": (
            "🚫 *البطاقة محظورة — لا توجد بيانات ثقة*\n\n"
            "تعذّر قياس الجودة. جرّب */modele turbo* أو */modele excellence* ثم أعد الإرسال."
        ),
        "audio_too_quiet_block": (
            "📉 *تم رفض الصوت — جودة غير كافية*\n\n"
            "{reason}\n\n"
            "بهذا المستوى من الصوت، Whisper سيهلوس. لم يتم خصم أي رصيد.\n\n"
            "*الحلول:*\n"
            "• 🎙️ أرسل الصوت *الأصلي* (إعادة التوجيه المتكررة تُتلف الجودة).\n"
            "• 🔊 ارفع مستوى الصوت قبل الإرسال (QuickTime / Audacity / ffmpeg).\n"
            "• 🌐 أو استخدم *تطبيق الويب* بصوت أفضل."
        ),
        "low_usable_ratio_block": (
            "🚫 *البطاقة محظورة — مقاطع كثيرة غير مسموعة*\n\n"
            "فقط *{usable_pct:.0f}%* من الدرس قابل للاستغلال "
            "({inaudible_min} د {inaudible_sec:02d}ث غير مسموعة من أصل {total_min} د).\n\n"
            "الحد الأدنى: *{threshold_pct:.0f}%* لإنشاء بطاقة موثوقة.\n\n"
            "*الحلول:*\n"
            "• 📝 */texte* — استرجاع النص الخام مع علامات `[غير مسموع MM:SS]`\n"
            "• 🔍 */confiance* — تفاصيل المقاطع المشكلة\n"
            "• 🎙️ أرسل تسجيلًا *آخر* لنفس الدرس بجودة أفضل"
        ),
        "progress_heartbeat": "⏳ لا يزال العمل جاريًا — *{phase}* ({pct}%)",
        "progress_heartbeat_nopct": "⏳ لا يزال العمل جاريًا — *{phase}*",
        "phase_received": "تم استلام الملف",
        "phase_preprocessing": "تحضير الصوت",
        "phase_whisper_chunk": "تقسيم إلى مقاطع",
        "phase_whisper": "النسخ بواسطة Whisper",
        "phase_whisper_complete": "اكتمل النسخ، جاري التنظيف",
        "phase_post_process": "ما بعد المعالجة (تنقيط، هيكلة)",
        "phase_running": "النسخ جارٍ",
        "phase_accepted": "في قائمة الانتظار",
        "unsupported_type": "عذرًا، هذا النوع غير مدعوم. أرسل لي *تسجيلًا صوتيًا* أو رسالة صوتية لدرسك 🎙️",
        "media_too_large": (
            "📦 *الملف كبير جدًا لتلغرام* (الحد: 20 ميغابايت للبوت).\n\n"
            "الحلول:\n"
            "• ✂️ *قسّم الصوت* إلى أجزاء (20-30 دقيقة لكل جزء) وأرسلها واحدًا تلو الآخر.\n"
            "• 🎙️ أو سجّله كـ *رسالة صوتية* داخل تلغرام (أيقونة الميكروفون) — التسجيل المباشر يضغط بصيغة OPUS ويبقى عادةً تحت الحد حتى لساعة كاملة.\n"
            "• 🌐 أو استخدم *تطبيق LecturAI على الويب* (حد أعلى): {signup_url}"
        ),
        "media_too_large_no_url": (
            "📦 *الملف كبير جدًا لتلغرام* (الحد: 20 ميغابايت للبوت).\n\n"
            "الحلول:\n"
            "• ✂️ قسّم الصوت إلى أجزاء (20-30 دقيقة لكل جزء) وأرسلها واحدًا تلو الآخر.\n"
            "• 🎙️ سجّله كـ *رسالة صوتية* (أيقونة الميكروفون) — صيغة OPUS تبقى عادةً تحت الحد."
        ),
    },
}


def t(key: str, ui_locale: Optional[str] = None, **fmt: object) -> str:
    """Look-up : surcharge TG d'abord, puis fallback WhatsApp, puis clé brute.

    Mêmes garanties que ``whatsapp.messages.t`` (jamais d'exception si placeholder absent).
    """
    lang = lang_for(ui_locale)
    overrides = _TG_OVERRIDES.get(lang) or _TG_OVERRIDES["fr"]
    if key in overrides:
        raw = overrides[key]
    else:
        bag = WA_MESSAGES.get(lang) or WA_MESSAGES["fr"]
        raw = bag.get(key) or _TG_OVERRIDES["fr"].get(key) or WA_MESSAGES["fr"].get(key) or key
    try:
        return raw.format(**fmt) if fmt else raw
    except (KeyError, IndexError):
        return raw
