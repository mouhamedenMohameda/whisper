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


async def _maybe_hint(phone: str, key: str, ui_loc: str, **fmt: Any) -> None:
    """Envoie une astuce contextuelle, au plus une fois par session pour un même ``key``."""
    seen = _hints_seen.setdefault(phone, set())
    if key in seen:
        return
    seen.add(key)
    msg = t(key, ui_loc, **fmt)
    if msg and msg != key:
        await _safe_send(phone, msg)


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


def _extract_quiz(lesson_md: str) -> list[dict[str, Any]]:
    """Extrait une liste de questions {question, options:[(letter,text)], correct, explanation}.

    On scanne le markdown ligne à ligne :
      - lignes A) … D) (avec ou sans ✅) → options
      - ligne précédente = énoncé de la question
      - première ligne après les options qui commence par 'Explication:' ou 'تفسير:' → explication
    """
    if not lesson_md:
        return []
    lines = lesson_md.splitlines()
    questions: list[dict[str, Any]] = []
    opt_re = re.compile(r"^\s*([A-D])\)\s*(.+?)\s*$")
    expl_re = re.compile(r"^\s*(?:Explication|تفسير)\s*:\s*(.+?)\s*$", re.IGNORECASE)

    i = 0
    while i < len(lines):
        if opt_re.match(lines[i]):
            # On a trouvé un A) — chercher la question juste avant (dernière ligne non vide).
            qline = ""
            j = i - 1
            while j >= 0:
                cand = lines[j].strip().lstrip("#").strip()
                if cand and not cand.startswith("Explication") and not cand.startswith("تفسير"):
                    qline = re.sub(r"^\s*(?:\d+[\.\)]\s*|Q\d*\s*[:\.]?\s*)", "", cand).strip("* ").strip()
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
                correct = next((let for let, _, ok in options if ok), options[0][0])
                questions.append({
                    "question": qline[:600],
                    "options": [(let, txt) for let, txt, _ in options],
                    "correct": correct,
                    "explanation": explanation[:600],
                })
            continue
        i += 1
    return questions[:10]  # cap raisonnable


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

    from course_from_transcript import run_course_pipeline
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

    from routes.generate import get_localized_lesson_system_prompt

    # Cible de longueur : ~50 % des mots du transcript, plancher 800 mots.
    transcript_words_estimate = max(1, len(transcript.split()))
    target_words = max(800, int(transcript_words_estimate * 0.5))

    prompt = get_localized_lesson_system_prompt(target_lang)
    prompt += (
        f"\n\nIMPORTANT: Write the entire lesson in {lang_name} (no English). "
        + "For the quiz section, keep options labeled exactly A) B) C) D) (Latin letters), "
        + "mark the correct choice with ✅, and prefix each answer explanation with "
        + f"'{expl_label}:'."
        + "\n\nRÉDIGE UNE FICHE RICHE ET DÉTAILLÉE — interdiction de produire une fiche squelettique :"
        + f"\n- Pour ce cours, vise au moins {target_words} mots dans la fiche."
        + "\n- Introduction substantielle (5–10 lignes) qui pose le contexte et les objectifs."
        + "\n- Au moins 4 à 8 sections (H2) avec sous-sections (H3) quand pertinent."
        + "\n- Pour chaque concept : définition claire + explication développée + au moins 1 exemple concret ou analogie."
        + "\n- Encadrés mémo, formules importantes, points de vigilance signalés."
        + "\n- Conclusion / synthèse récapitulative avec les 5 idées-clés à retenir."
        + "\n- N'invente PAS de contenu absent du transcript — développe et structure ce qui est dit."
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

    try:
        lesson_md, inp, out, _meta = await asyncio.to_thread(
            run_course_pipeline,
            api_key=api_key,
            subject=job.subject or "General",
            transcript=transcript,
            asr_passages_annotated=asr,
            transcript_mixed_view=mixed,
            lesson_system_prompt=prompt,
            model=model,
            max_tokens_lesson=max_tokens,
        )
    except Exception as exc:
        logger.exception("run_course_pipeline a échoué public_id=%s", job.public_id)
        return None, f"LLM Groq : {type(exc).__name__} — {str(exc)[:160]}"

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
