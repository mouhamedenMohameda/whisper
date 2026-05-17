"""Orchestrateur du bot Telegram — appelé en background task depuis le webhook.

Symétrique de ``whatsapp.processor`` mais beaucoup plus court car :
  - Pas de question de matière interactive (v2) — on prend ``user.telegram_subject`` ou auto-détection.
  - Pas d'upload média 2-étapes (Telegram ``sendDocument`` accepte multipart direct).
  - Pas de HMAC du body (Telegram = secret_token simple, géré dans la route).
  - Quiz : ``InlineKeyboardButton`` (callback_query) — équivalent direct aux *list reply* WhatsApp.

Toute la logique métier "transcription → fiche → quiz" est déléguée à ``services.lesson_pipeline``,
qui ré-exporte les fonctions pures de ``whatsapp.processor``. On évite ainsi la duplication.

Flow audio :
  1. Lookup user par ``telegram_chat_id`` (sinon → /lier).
  2. Vérifier portefeuille (sinon → message recharge).
  3. Anti-spam : rate-limit 30s par chat.
  4. ACK rapide.
  5. Télécharger via ``getFile`` → ``data/jobs/<public_id>/upload.<ext>``.
  6. Créer ``TranscriptionJob`` (``source='telegram'``).
  7. Spawn ``execute_transcription_job``.
  8. Poll jusqu'à done/failed/timeout → envoi menu post-transcription.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from credits_wallet import wallet_block_reason
from database import SessionLocal
from models import TranscriptionJob, User
from pricing import wallet_units_to_mru_display
from services import lesson_pipeline
from whatsapp.messages import lang_for
from whatsapp.processor import _WA_MODEL_ALIASES, _WA_DEFAULT_MODEL  # alias + défaut partagés

from . import client as tg_client
from . import config as tg_config
from .messages import t
from .parser import InboundTgMessage

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"
_RATE_LIMIT_SECONDS = 30.0
_PROCESS_TIMEOUT_SECONDS = float(os.getenv("TELEGRAM_PROCESS_TIMEOUT_SEC", "900"))
_DELIVERY_TIMEOUT_SECONDS = float(os.getenv("TELEGRAM_DELIVERY_TIMEOUT_SEC", "7200"))
_TRANSCRIBE_POLL_INTERVAL = 2.5

# Rate-limit in-memory ({chat_id: last_audio_epoch}). 1 worker uvicorn assumé.
_last_audio_at: dict[str, float] = {}

# Sessions de quiz en mémoire — {chat_id: {questions, idx, score, ui_loc, job_public_id}}.
_quiz_sessions: dict[str, dict[str, Any]] = {}


# =============================================================================
# Helpers
# =============================================================================

async def _safe_send(chat_id: str, body: str) -> None:
    """Send qui ne plante pas si Telegram refuse — log et continue."""
    try:
        await tg_client.send_text(chat_id, body)
    except Exception:
        logger.exception("send_text Telegram échoué chat=%s", chat_id)


def _tg_user_model_id(user: User) -> str:
    """Modèle Telegram préféré de l'user, validé contre le catalogue actuel."""
    from transcription_retail_catalog import RETAIL_MODELS

    stored = getattr(user, "telegram_transcription_model", None)
    if stored and stored in RETAIL_MODELS:
        return stored
    return _WA_DEFAULT_MODEL


def _is_rate_limited(chat_id: str) -> bool:
    now = time.monotonic()
    last = _last_audio_at.get(chat_id, 0.0)
    if now - last < _RATE_LIMIT_SECONDS:
        return True
    _last_audio_at[chat_id] = now
    return False


def _guess_user_locale(db: Session, user: User) -> Optional[str]:
    """Préférence explicite → dernière ``ui_locale`` connue (tous jobs confondus)."""
    explicit = getattr(user, "telegram_language", None) or getattr(user, "whatsapp_language", None)
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


def _too_large_message(ui_loc: str) -> str:
    """Construit le message 'fichier trop gros' avec ou sans lien vers l'app web selon config."""
    signup = tg_config.signup_url()
    if signup:
        return t("media_too_large", ui_loc, signup_url=signup)
    return t("media_too_large_no_url", ui_loc)


def _ext_from_mime(mime: Optional[str]) -> str:
    """Devine une extension fichier raisonnable. Défaut .ogg (format des voice Telegram)."""
    if not mime:
        return ".ogg"
    m = mime.lower()
    if "ogg" in m or "opus" in m:
        return ".ogg"
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "wav" in m:
        return ".wav"
    if "m4a" in m or "mp4" in m or "aac" in m:
        return ".m4a"
    if "webm" in m:
        return ".webm"
    if "flac" in m:
        return ".flac"
    return ".ogg"


# =============================================================================
# Point d'entrée + dispatch
# =============================================================================

async def handle_inbound(msg: InboundTgMessage) -> None:
    """Background task déclenchée depuis le webhook."""
    if not msg or not msg.chat_id:
        return
    db: Session = SessionLocal()
    try:
        await asyncio.wait_for(_dispatch(db, msg), timeout=_PROCESS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("Telegram process timeout chat=%s", msg.chat_id)
        await _safe_send(
            msg.chat_id,
            t("transcribe_failed_with_reason", "fr",
              reason=f"Délai dépassé ({int(_PROCESS_TIMEOUT_SECONDS)}s) — audio trop long ou worker saturé."),
        )
    except Exception as exc:
        logger.exception("Telegram dispatch exception chat=%s", msg.chat_id)
        reason = f"Erreur interne : {type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(msg.chat_id, t("transcribe_failed_with_reason", "fr", reason=reason))
    finally:
        db.close()


async def _dispatch(db: Session, msg: InboundTgMessage) -> None:
    chat_id = msg.chat_id

    # === Callback (clic bouton inline) ===
    if msg.type == "callback":
        # ACK obligatoire pour stopper le spinner côté user — best-effort.
        if msg.callback_id:
            await tg_client.answer_callback_query(msg.callback_id)
        cb_data = msg.callback_data or ""
        # Quiz : "quiz:<idx>:<letter>"
        if cb_data.startswith("quiz:"):
            user = _lookup_user_by_chat(db, chat_id)
            if user is None:
                return  # session quiz orpheline si l'user a délié son compte — silencieux
            await _handle_quiz_reply(chat_id, cb_data)
            return
        return

    # === Deep link de liaison : /start <token> avant le lookup user ===
    # On gère ce cas spécifique en premier car le user n'est pas encore lié au chat.
    if msg.type == "text" and msg.text:
        start_token = _extract_start_token(msg.text)
        if start_token is not None:
            await _handle_link_token(db, chat_id, start_token, tg_config.default_language())
            return

    # === Lookup user ===
    user = _lookup_user_by_chat(db, chat_id)
    locale_hint = tg_config.default_language()

    # === Cas 1 : chat inconnu ===
    if user is None:
        # On accepte uniquement /lier (instructions) et /start nu — tout le reste → bienvenue.
        if msg.type == "text" and (msg.text or "").lower().startswith("/lier"):
            await _handle_lier_command(db, chat_id, locale_hint)
            return
        signup = tg_config.signup_url()
        if signup:
            await _safe_send(chat_id, t("welcome_unknown", locale_hint, signup_url=signup))
        else:
            await _safe_send(chat_id, t("welcome_unknown_no_url", locale_hint))
        return

    ui_loc = _guess_user_locale(db, user) or locale_hint

    # === Texte / commandes ===
    if msg.type == "text" and msg.text:
        cmd_raw = msg.text.strip()
        cmd = cmd_raw.lower()
        # Telegram suffix de bot : "/aide@LecturAIBot" → on normalise.
        cmd = re.sub(r"@[a-z0-9_]+\b", "", cmd, count=1)

        if cmd in ("/start", "/aide", "/help", "/aide@lecturaibot", "start", "aide", "help"):
            await _safe_send(chat_id, t("help_text", ui_loc))
            return
        if cmd.startswith("/lier"):
            await _handle_lier_command(db, chat_id, ui_loc)
            return
        if cmd in ("/delier", "/unlink"):
            await _handle_delier_command(db, user, chat_id, ui_loc)
            return
        if cmd in ("/solde", "/balance"):
            await _handle_solde_command(user, chat_id, ui_loc)
            return
        if cmd.startswith("/modele") or cmd.startswith("/modèle"):
            await _handle_modele_command(db, user, chat_id, ui_loc, cmd_raw)
            return
        if cmd.startswith("/matiere") or cmd.startswith("/matière"):
            await _handle_matiere_command(db, user, chat_id, ui_loc, cmd_raw)
            return
        if cmd.startswith("/langue"):
            await _handle_langue_command(db, user, chat_id, ui_loc, cmd_raw)
            return
        if cmd in ("/pdf", "/fiche", "/cours"):
            await _handle_pdf_command(user, chat_id, ui_loc)
            return
        if cmd in ("/refaire", "/regen", "/retry"):
            await _handle_refaire_command(user, chat_id, ui_loc)
            return
        if cmd in ("/texte", "/text", "/transcript"):
            await _handle_texte_command(user, chat_id, ui_loc)
            return
        if cmd == "/quiz":
            await _handle_quiz_command(user, chat_id, ui_loc)
            return
        if cmd in ("/partage", "/share", "/lien"):
            await _handle_partage_command(user, chat_id, ui_loc)
            return
        if cmd in ("/confiance", "/confidence", "/qualite", "/qualité"):
            await _handle_confiance_command(user, chat_id, ui_loc)
            return

        # Texte non reconnu → /aide
        await _safe_send(chat_id, t("help_text", ui_loc))
        return

    # === Audio / vocal / document audio ===
    if msg.type in ("voice", "audio", "document"):
        if _is_rate_limited(chat_id):
            await _safe_send(chat_id, t("rate_limited", ui_loc))
            return
        block = wallet_block_reason(user)
        if block:
            topup = tg_config.topup_url()
            if topup:
                await _safe_send(chat_id, t("wallet_blocked", ui_loc, topup_url=topup))
            else:
                await _safe_send(chat_id, t("wallet_blocked_no_url", ui_loc))
            return
        await _handle_audio_inbound(user, chat_id, ui_loc, msg)
        return

    # Type non supporté
    await _safe_send(chat_id, t("unsupported_type", ui_loc))


def _lookup_user_by_chat(db: Session, chat_id: str) -> Optional[User]:
    return db.execute(select(User).where(User.telegram_chat_id == chat_id)).scalar_one_or_none()


# =============================================================================
# Liaison de compte — deep link signé depuis l'app web (sécurisé)
# =============================================================================
#
# Pourquoi un deep link et pas /lier <numéro> :
#   Sans preuve de possession, n'importe qui peut taper /lier <numéro_d'un_autre> et
#   binder le compte de la victime à son chat Telegram → drainage des crédits.
#   Le deep link garantit la propriété : seul un user authentifié dans l'app peut
#   générer le token (cf. routes/telegram.py:issue_link_token).
#
# Format du token côté URL : `t.me/lecturai_bot?start=<token>` → Telegram envoie
# au bot `/start <token>`. Le token est consommé une fois (effacé en DB après bind).

_LINK_TOKEN_RE = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?\s+(\S+)\s*$", re.IGNORECASE)


def _extract_start_token(text: str) -> Optional[str]:
    """Retourne le payload de ``/start <token>`` (ou ``/start@bot <token>``), sinon None.

    Un ``/start`` nu (sans argument) → None : on tombera dans le flow help_text classique.
    """
    m = _LINK_TOKEN_RE.match(text.strip())
    if not m:
        return None
    token = m.group(1).strip()
    # Telegram limite ``start`` à 64 chars. On accepte un peu plus pour tolérance, mais on jette
    # tout ce qui ne ressemble pas à un token URL-safe (anti-injection paranoia).
    if len(token) > 96 or not re.match(r"^[A-Za-z0-9_\-]+$", token):
        return None
    return token


def _hash_link_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _handle_link_token(db: Session, chat_id: str, token: str, ui_loc: str) -> None:
    """Consomme un token de liaison généré par ``/api/telegram/link-token``.

    Validation :
      1. Le hash correspond à un user en DB.
      2. ``telegram_link_token_expires_at`` est dans le futur.
      3. Si ce chat est déjà lié au même user → idempotent (renvoie "déjà lié").
      4. Si ce chat est lié à un *autre* user → refus (l'user doit /delier l'autre compte).
      5. Si l'user a déjà un autre chat → refus (cohérent : un compte = un chat).

    Token effacé en DB qu'il soit consommé ou refusé pour expiration — pas de re-use.
    """
    token_hash = _hash_link_token(token)
    user = db.execute(
        select(User).where(User.telegram_link_token_hash == token_hash)
    ).scalar_one_or_none()

    # Token inconnu → message générique (on ne distingue pas "n'a jamais existé" d'"expiré"
    # côté user, mais on log la différence côté serveur pour le debug).
    if user is None:
        logger.info("Telegram link token introuvable (peut-être déjà consommé) chat=%s", chat_id)
        await _safe_send(chat_id, t("lier_token_invalid", ui_loc))
        return

    # Vérification expiration. ``expires_at`` peut être naïf (SQLite) ou aware (PG) — on normalise.
    expires_at = user.telegram_link_token_expires_at
    now = datetime.now(timezone.utc)
    if expires_at is None or _to_aware_utc(expires_at) < now:
        logger.info("Telegram link token expiré user_id=%s chat=%s", user.id, chat_id)
        # On efface pour éviter qu'un attaquant qui obtiendrait un vieux token l'utilise.
        user.telegram_link_token_hash = None
        user.telegram_link_token_expires_at = None
        db.commit()
        await _safe_send(chat_id, t("lier_token_invalid", ui_loc))
        return

    # === Cohérence : un compte = un chat ===
    if user.telegram_chat_id and user.telegram_chat_id == chat_id:
        # Idempotent : l'user a re-cliqué sur le lien après une liaison réussie.
        user.telegram_link_token_hash = None
        user.telegram_link_token_expires_at = None
        db.commit()
        await _safe_send(chat_id, t("lier_chat_already_linked", ui_loc))
        return
    if user.telegram_chat_id and user.telegram_chat_id != chat_id:
        # Le compte est déjà lié à un autre chat — on n'écrase pas silencieusement.
        await _safe_send(chat_id, t("lier_already_linked_other", ui_loc))
        return

    # === Ce chat est-il déjà associé à un AUTRE compte ? ===
    other = _lookup_user_by_chat(db, chat_id)
    if other is not None and other.id != user.id:
        await _safe_send(chat_id, t("lier_already_linked_other", ui_loc))
        return

    # === Bind + invalidation du token ===
    user.telegram_chat_id = chat_id
    user.telegram_link_token_hash = None
    user.telegram_link_token_expires_at = None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        await _safe_send(chat_id, t("lier_already_linked_other", ui_loc))
        return

    logger.info("Telegram lié user_id=%s chat=%s via deep link", user.id, chat_id)
    await _safe_send(chat_id, t("lier_ok", ui_loc))


def _to_aware_utc(dt: datetime) -> datetime:
    """Postgres renvoie aware, SQLite naïf. On normalise en UTC aware pour comparer."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _handle_lier_command(db: Session, chat_id: str, ui_loc: str) -> None:
    """``/lier`` sans argument — affiche les instructions pour passer par l'app web.

    Le binding direct par numéro (ancien comportement) est **désactivé** : c'était un vecteur
    de fraude trivial (n'importe qui peut taper le numéro d'un autre user).
    """
    existing = _lookup_user_by_chat(db, chat_id)
    if existing is not None:
        await _safe_send(chat_id, t("lier_chat_already_linked", ui_loc))
        return
    signup = tg_config.signup_url()
    key = "lier_usage" if signup else "lier_usage_no_url"
    await _safe_send(chat_id, t(key, ui_loc, signup_url=signup or ""))


async def _handle_delier_command(db: Session, user: User, chat_id: str, ui_loc: str) -> None:
    """``/delier`` — délie ce chat du compte. L'user peut ensuite re-lier (avec un nouveau token)."""
    if not user.telegram_chat_id:
        await _safe_send(chat_id, t("delier_not_linked", ui_loc))
        return
    user.telegram_chat_id = None
    user.telegram_link_token_hash = None
    user.telegram_link_token_expires_at = None
    db.commit()
    await _safe_send(chat_id, t("delier_ok", ui_loc))


# =============================================================================
# Commandes de réglages (/solde, /modele, /matiere, /langue)
# =============================================================================

async def _handle_solde_command(user: User, chat_id: str, ui_loc: str) -> None:
    mru = wallet_units_to_mru_display(int(user.credit_balance or 0))
    await _safe_send(chat_id, t("balance_line", ui_loc, mru=f"{mru:.2f}"))
    block = wallet_block_reason(user)
    if block:
        topup = tg_config.topup_url()
        if topup:
            await _safe_send(chat_id, t("hint_balance_low", ui_loc, topup_url=topup))


async def _handle_modele_command(db: Session, user: User, chat_id: str, ui_loc: str, cmd: str) -> None:
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
        current_id = _tg_user_model_id(user)
        await _safe_send(
            chat_id,
            t("modele_list", ui_loc,
              current_label=_label(current_id),
              current_mru=_price(current_id),
              p_turbo=_price("whisper-large-v3-turbo"),
              p_large=_price("whisper-large-v3"),
              p_4omini=_price("gpt-4o-mini-transcribe"),
              p_w1=_price("whisper-1")),
        )
        return

    target_id = _WA_MODEL_ALIASES.get(arg) or (arg if arg in RETAIL_MODELS else None)
    if target_id is None:
        await _safe_send(chat_id, t("modele_unknown", ui_loc, alias=arg))
        return

    user.telegram_transcription_model = target_id
    db.commit()
    await _safe_send(chat_id, t("modele_set_ok", ui_loc, label=_label(target_id), mru=_price(target_id)))


async def _handle_matiere_command(db: Session, user: User, chat_id: str, ui_loc: str, cmd: str) -> None:
    parts = cmd.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        current = getattr(user, "telegram_subject", None) or "auto"
        await _safe_send(chat_id, t("matiere_current", ui_loc, current=current))
        return
    if arg.lower() in ("auto", "automatique", "تلقائي"):
        user.telegram_subject = None
        db.commit()
        await _safe_send(chat_id, t("matiere_auto_ok", ui_loc))
        return
    subject = arg[:128]
    user.telegram_subject = subject
    db.commit()
    await _safe_send(chat_id, t("matiere_set_ok", ui_loc, subject=subject))


async def _handle_langue_command(db: Session, user: User, chat_id: str, ui_loc: str, cmd: str) -> None:
    parts = cmd.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    if not arg:
        current = "Français" if ui_loc.startswith("fr") else "العربية"
        await _safe_send(chat_id, t("langue_current", ui_loc, current=current))
        return
    if arg.startswith("fr"):
        user.telegram_language = "fr"
        db.commit()
        await _safe_send(chat_id, t("langue_set_ok", "fr", lang="Français"))
        return
    if arg.startswith("ar"):
        user.telegram_language = "ar"
        db.commit()
        await _safe_send(chat_id, t("langue_set_ok", "ar", lang="العربية"))
        return
    await _safe_send(chat_id, t("langue_unknown", ui_loc))


# =============================================================================
# Pipeline audio
# =============================================================================

async def _handle_audio_inbound(user: User, chat_id: str, ui_loc: str, msg: InboundTgMessage) -> None:
    """Télécharge l'audio Telegram et lance un TranscriptionJob — sans question de matière (v1).

    La matière est prise depuis ``user.telegram_subject`` (si posée) ou auto-détectée après la
    transcription via ``services.lesson_pipeline.detect_subject_from_text``.
    """
    if not msg.file_id:
        await _safe_send(chat_id, t("unsupported_type", ui_loc))
        return

    # Pré-check taille : l'API Bot Telegram standard plafonne ``getFile`` à 20 MB. Si Telegram a
    # fourni ``file_size`` dans l'update (audio/document — pas toujours pour voice), on rejette
    # immédiatement avec un message explicatif (limite + solutions) plutôt que d'attendre le 400.
    if msg.file_size is not None and msg.file_size > tg_client.max_media_bytes():
        logger.info(
            "Telegram audio ignoré (trop gros) chat=%s file_id=%s size=%d MB",
            chat_id, msg.file_id, msg.file_size // (1024 * 1024),
        )
        await _safe_send(chat_id, _too_large_message(ui_loc))
        return

    public_id = uuid.uuid4().hex[:32]
    ext = _ext_from_mime(msg.file_mime)
    job_dir = _DATA / "jobs" / public_id
    job_dir.mkdir(parents=True, exist_ok=True)
    upload_path = job_dir / f"upload{ext}"

    await _safe_send(chat_id, t("ack_audio", ui_loc))

    try:
        await tg_client.download_file(msg.file_id, upload_path)
    except tg_client.TelegramApiError as exc:
        if exc.status == 413:
            # "File is too big" remonté en aval (file_size pas fourni dans l'update — cas voice).
            logger.info("Telegram download rejeté par l'API (trop gros) chat=%s file_id=%s", chat_id, msg.file_id)
            await _safe_send(chat_id, _too_large_message(ui_loc))
        else:
            await _safe_send(chat_id, t("download_failed_with_reason", ui_loc, reason=str(exc)[:200]))
        return
    except Exception as exc:
        logger.exception("Telegram download_file échoué chat=%s file_id=%s", chat_id, msg.file_id)
        await _safe_send(chat_id, t("download_failed_with_reason", ui_loc,
                                    reason=f"{type(exc).__name__} — {str(exc)[:160]}"))
        return

    # === Pré-vérification audio : on rejette les fichiers manifestement trop bas en volume
    # AVANT de créer le job → 0 débit MRU et message immédiat. Pré-check ~1-2s. ===
    try:
        from audio_prevalidation import analyze_audio

        analysis = await asyncio.to_thread(analyze_audio, upload_path, sample_seconds=60)
        if analysis.measured and not analysis.is_acceptable:
            logger.info(
                "Telegram audio rejeté (volume) chat=%s mean=%s max=%s reason=%s",
                chat_id, analysis.mean_volume_db, analysis.max_volume_db, analysis.reason,
            )
            await _safe_send(chat_id, t("audio_too_quiet_block", ui_loc, reason=analysis.reason))
            # On nettoie le fichier qu'on vient de télécharger — pas de job, pas besoin.
            try:
                upload_path.unlink()
            except OSError:
                pass
            return
    except Exception:
        # Toute erreur dans le pré-check ne doit pas empêcher la transcription — on laisse passer.
        logger.exception("Audio prevalidation a planté — on continue (audio sera transcrit)")

    initial_subject = getattr(user, "telegram_subject", None) or "General"
    explicit_subject = bool(getattr(user, "telegram_subject", None))

    await _run_audio_pipeline_for_user(
        chat_id=chat_id,
        ui_loc=ui_loc,
        user_id=user.id,
        public_id=public_id,
        rel_path=f"jobs/{public_id}/{upload_path.name}",
        mime=msg.file_mime,
        message_id=msg.message_id,
        initial_subject=initial_subject,
        explicit_subject=explicit_subject,
    )


async def _run_audio_pipeline_for_user(
    *,
    chat_id: str,
    ui_loc: str,
    user_id: int,
    public_id: str,
    rel_path: str,
    mime: Optional[str],
    message_id: str,
    initial_subject: str,
    explicit_subject: bool,
) -> None:
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
            original_filename=f"telegram_{message_id[:24]}{ext}"[:384],
            subject=initial_subject or "General",
            speech_language=speech_lang,
            ui_locale=ui_loc[:16],
            transcription_engine=_tg_user_model_id(user),
            input_relpath=rel_path,
            client_content_type=(mime or "")[:160] or None,
            status="queued",
            progress_percent=1,
            phase="received",
            status_message="Reçu via Telegram — en file d'attente.",
            source="telegram",
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
        )
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(
                select(TranscriptionJob).where(TranscriptionJob.telegram_message_id == message_id)
            ).scalar_one_or_none()
            if existing is None:
                logger.exception("IntegrityError sans job existant — abandon msg_id=%s", message_id)
                return
            logger.info("Telegram duplicate ignored msg_id=%s public_id=%s", message_id, existing.public_id)
            return
        db.refresh(job)
        model_label = _tg_user_model_id(user)
    finally:
        db.close()

    from routes import transcribe_jobs as tj

    asyncio.create_task(tj.execute_transcription_job(public_id))
    await _safe_send(chat_id, t("progress_transcribing", ui_loc, model=model_label))

    # Tâche détachée : attend la fin et envoie le menu (timeout long pour audios 2h).
    asyncio.create_task(
        _deliver_lesson_after_transcription(
            chat_id=chat_id, ui_loc=ui_loc, public_id=public_id, explicit_subject=explicit_subject
        )
    )


async def _deliver_lesson_after_transcription(
    *, chat_id: str, ui_loc: str, public_id: str, explicit_subject: bool
) -> None:
    try:
        final_job = await _wait_with_progress(
            public_id, chat_id=chat_id, ui_loc=ui_loc, timeout_seconds=_DELIVERY_TIMEOUT_SECONDS,
        )
        if final_job is None or final_job.status != "done":
            err = (final_job.error_detail if final_job else None) or "Aucune réponse du worker (timeout)."
            logger.warning("Transcription Telegram failed public_id=%s err=%s", public_id, err)
            await _safe_send(chat_id, t("transcribe_failed_with_reason", ui_loc, reason=str(err)[:300]))
            return

        # Auto-détection matière si l'user n'a pas précisé.
        if not explicit_subject and (final_job.subject or "General") == "General":
            import json as _json
            try:
                payload = _json.loads(final_job.result_json) if final_job.result_json else {}
            except Exception:
                payload = {}
            transcript_for_detect = (payload.get("transcript") or "") if isinstance(payload, dict) else ""
            detected = lesson_pipeline.detect_subject_from_text(transcript_for_detect)
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

        subject = final_job.subject or "General"
        await _safe_send(chat_id, t("transcription_ready", ui_loc, subject=subject))

        # Avis post-transcription :
        #   - Si trop de zones inaudibles (ratio utilisable < seuil) → blocage anticipé immédiat
        #     pour éviter que l'user lance /pdf et se prenne le blocage en pleine face.
        #   - Sinon, on affiche le menu normal. Si quelques zones inaudibles existent mais
        #     que le ratio reste OK, on prévient mais on laisse passer.
        inaudible = _get_job_inaudible_summary(final_job)
        usable_min = _pdf_min_usable_ratio()

        if isinstance(inaudible, dict):
            usable_ratio = float(inaudible.get("usable_ratio") or 1.0)
            inaudible_sec = float(inaudible.get("inaudible_duration_sec") or 0)
            total_sec = float(inaudible.get("total_duration_sec") or 0)
            if usable_min > 0 and usable_ratio < usable_min:
                # Blocage anticipé — pas de menu.
                await _safe_send(
                    chat_id,
                    t("low_usable_ratio_block", ui_loc,
                      usable_pct=usable_ratio * 100,
                      inaudible_min=int(inaudible_sec // 60),
                      inaudible_sec=int(inaudible_sec % 60),
                      total_min=int(total_sec // 60),
                      threshold_pct=usable_min * 100),
                )
                return
            if inaudible_sec > 30 and total_sec > 0:
                # On a quelques zones inaudibles mais le ratio est OK → on prévient juste.
                await _safe_send(
                    chat_id,
                    f"ℹ️ *{int(inaudible_sec // 60)}m{int(inaudible_sec % 60):02d}s* inaudibles "
                    f"détectées dans l'enregistrement ({(1 - usable_ratio) * 100:.0f}% du cours). "
                    f"La fiche couvrira uniquement les parties exploitables.\n"
                    f"Détails : /confiance"
                    if not ui_loc.startswith("ar")
                    else f"ℹ️ تم اكتشاف *{int(inaudible_sec // 60)} د {int(inaudible_sec % 60):02d} ث* "
                    f"غير مسموعة ({(1 - usable_ratio) * 100:.0f}% من الدرس). البطاقة ستغطي الأجزاء "
                    f"القابلة للاستغلال فقط. التفاصيل : /confiance",
                )

        await _safe_send(chat_id, t("menu_after_transcription", ui_loc))
    except Exception as exc:
        logger.exception("Tâche post-transcription Telegram a planté public_id=%s", public_id)
        reason = f"{type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(chat_id, t("generate_failed_with_reason", ui_loc, reason=reason))


async def _wait_for_transcription(public_id: str, timeout_seconds: float) -> Optional[TranscriptionJob]:
    """Variante silencieuse : poll jusqu'à statut terminal, pas de heartbeat. Gardée pour
    les commandes /pdf, /quiz qui re-générent à partir d'un job déjà ``done`` (pas d'attente)."""
    elapsed = 0.0
    while elapsed < timeout_seconds:
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


# Intervalle minimum entre 2 heartbeats consécutifs. 60s = compromis :
#   - assez fréquent pour rassurer l'user (pas 5 min de silence pendant Whisper)
#   - pas trop pour ne pas spammer (Telegram rate limit 1 msg/s même chat, mais surtout
#     l'user n'a pas envie de voir son écran clignoter toutes les 10s)
_HEARTBEAT_INTERVAL_SECONDS = 60.0
# Si la phase change, on notifie même si <60s écoulées (transitions intéressantes :
# preprocessing → whisper → post_process). Mais on attend au moins ce délai entre 2 *changements
# de phase* pour ne pas spammer si la pipeline yo-yo entre phases rapprochées.
_HEARTBEAT_MIN_INTERVAL_ON_CHANGE = 8.0


async def _wait_with_progress(
    public_id: str, *, chat_id: str, ui_loc: str, timeout_seconds: float,
) -> Optional[TranscriptionJob]:
    """Poll la DB + envoie un heartbeat au chat quand la phase change ou toutes les ~60s.

    Pourquoi pas juste utiliser ``progress_percent`` : la pipeline ne le met pas à jour
    fréquemment (saut de 2% à 80% par exemple), donc on s'appuie d'abord sur ``phase`` (qui
    change à chaque étape majeure) et on tombe sur un timer en backup.
    """
    elapsed = 0.0
    last_phase: Optional[str] = None
    last_pct: int = -1
    last_ping_at = time.monotonic()

    while elapsed < timeout_seconds:
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
            cur_phase = job.phase
            cur_pct = int(job.progress_percent or 0)
        finally:
            db.close()

        now = time.monotonic()
        phase_changed = (cur_phase is not None and cur_phase != last_phase)
        time_for_periodic = (now - last_ping_at) >= _HEARTBEAT_INTERVAL_SECONDS

        # Heartbeat conditions :
        #   - phase changement (avec petit debounce pour éviter des bursts)
        #   - OU 60s écoulés sans ping (l'user doit voir que le bot bouge encore)
        should_ping = False
        if phase_changed and (now - last_ping_at) >= _HEARTBEAT_MIN_INTERVAL_ON_CHANGE:
            should_ping = True
        elif time_for_periodic:
            should_ping = True

        if should_ping:
            label = _phase_label(cur_phase, ui_loc)
            if cur_pct > 0 and cur_pct < 100:
                msg_body = t("progress_heartbeat", ui_loc, phase=label, pct=cur_pct)
            else:
                msg_body = t("progress_heartbeat_nopct", ui_loc, phase=label)
            await _safe_send(chat_id, msg_body)
            last_ping_at = now
            last_phase = cur_phase
            last_pct = cur_pct

    return None


def _phase_label(phase: Optional[str], ui_loc: str) -> str:
    """Mappe un nom de phase ASR (technique) vers un libellé user-friendly localisé.

    Phase inconnue → on renvoie le nom brut (mieux que rien, et signale un éventuel oubli côté code).
    """
    if not phase:
        return t("phase_running", ui_loc)
    key = f"phase_{phase}"
    label = t(key, ui_loc)
    # Si la clé n'existe pas, ``t()`` renvoie la clé brute → on détecte et fallback.
    if label == key:
        return phase
    return label


# =============================================================================
# Commandes post-transcription : /pdf, /refaire, /texte, /quiz, /partage
# =============================================================================

def _last_done_job(db: Session, user_id: int) -> Optional[TranscriptionJob]:
    return db.execute(
        select(TranscriptionJob)
        .where(TranscriptionJob.user_id == user_id)
        .where(TranscriptionJob.status == "done")
        .order_by(TranscriptionJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _pdf_min_confidence() -> float:
    """Seuil de confiance global (legacy, conservé pour rétrocompat).

    Configurable via ``TRANSCRIBE_PDF_MIN_CONFIDENCE`` (par défaut 0 = désactivé : on utilise
    désormais le seuil basé sur la durée utilisable, plus pertinent).
    """
    try:
        return float(os.getenv("TRANSCRIBE_PDF_MIN_CONFIDENCE", "0") or "0")
    except ValueError:
        return 0.0


def _pdf_min_usable_ratio() -> float:
    """Ratio minimum de durée *utilisable* (segments non marqués inaudibles) pour autoriser /pdf.

    Par défaut 0.60 = on accepte de générer une fiche tant qu'au moins 60 % du cours est exploitable.
    En dessous, on refuse : trop de trous, la fiche serait majoritairement vide.

    Configurable via ``TRANSCRIBE_PDF_MIN_USABLE_RATIO``. Mettre 0 désactive ce garde-fou aussi.
    """
    try:
        return float(os.getenv("TRANSCRIBE_PDF_MIN_USABLE_RATIO", "0.6") or "0.6")
    except ValueError:
        return 0.6


def _get_job_confidence(job: TranscriptionJob) -> Optional[dict]:
    """Lit le ``confidence_summary`` du job (depuis ``result_json``). Le recalcule si absent."""
    import json as _json

    if not job or not job.result_json:
        return None
    try:
        payload = _json.loads(job.result_json)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    summary = payload.get("confidence_summary")
    if isinstance(summary, dict) and summary.get("overall_score_0_100") is not None:
        return summary

    # Job ancien sans confidence_summary calculé — on le recalcule à la volée.
    try:
        from asr_confidence import compute_overall
        passages = payload.get("asr_passages_annotated") or []
        return compute_overall(passages)
    except Exception:
        logger.exception("Recalcul confidence_summary échoué public_id=%s", job.public_id)
        return None


def _get_job_inaudible_summary(job: TranscriptionJob) -> Optional[dict]:
    """Lit le ``inaudible_summary`` du job (produit par le segment cleaner)."""
    import json as _json

    if not job or not job.result_json:
        return None
    try:
        payload = _json.loads(job.result_json)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    s = payload.get("inaudible_summary")
    return s if isinstance(s, dict) else None


async def _enforce_min_confidence_or_block(
    user: User, chat_id: str, ui_loc: str, command_name: str
) -> bool:
    """Bloque ``/pdf`` / ``/quiz`` / ``/partage`` si la fiche ne peut pas être générée honnêtement.

    Stratégie nouvelle (depuis le segment cleaner) :
      1. Si on a un ``inaudible_summary`` → on regarde le **ratio de durée utilisable**.
         Sous le seuil (60 % par défaut) → blocage. Au-dessus → on laisse passer même si la
         confiance globale est modeste (la fiche couvrira juste les parties exploitables).
      2. Sinon (jobs anciens, modèles sans métriques) → fallback sur ``confidence_summary``
         + l'ancien seuil ``TRANSCRIBE_PDF_MIN_CONFIDENCE`` si > 0.

    Retourne ``True`` si la commande doit être stoppée (message déjà envoyé à l'user).
    """
    db = SessionLocal()
    try:
        job = _last_done_job(db, user.id)
        if job is None:
            return False  # le handler appelant gérera "pas de transcription"
        inaudible = _get_job_inaudible_summary(job)
        confidence = _get_job_confidence(job)
    finally:
        db.close()

    # === Stratégie 1 : ratio utilisable (préféré) ===
    if isinstance(inaudible, dict):
        usable_ratio = float(inaudible.get("usable_ratio") or 0)
        usable_min = _pdf_min_usable_ratio()
        if usable_min > 0 and usable_ratio < usable_min:
            inaudible_sec = float(inaudible.get("inaudible_duration_sec") or 0)
            total_sec = float(inaudible.get("total_duration_sec") or 0)
            logger.info(
                "Telegram %s bloqué (usable_ratio %.2f < %.2f) user=%s",
                command_name, usable_ratio, usable_min, user.id,
            )
            await _safe_send(
                chat_id,
                t("low_usable_ratio_block", ui_loc,
                  usable_pct=usable_ratio * 100,
                  inaudible_min=int(inaudible_sec // 60),
                  inaudible_sec=int(inaudible_sec % 60),
                  total_min=int(total_sec // 60),
                  threshold_pct=usable_min * 100),
            )
            return True
        # Ratio OK → on laisse passer même si la confiance globale brute est moyenne. Les
        # parties exploitables seront couvertes, les inaudibles sautées par Groq.
        return False

    # === Stratégie 2 (legacy) : confidence_summary brut ===
    threshold = _pdf_min_confidence()
    if threshold <= 0:
        return False
    if not confidence or confidence.get("overall_score_0_100") is None:
        logger.info("Telegram %s bloqué (pas de score conf) user=%s", command_name, user.id)
        await _safe_send(chat_id, t("low_confidence_block_unknown", ui_loc))
        return True
    score = float(confidence["overall_score_0_100"])
    if score >= threshold:
        return False
    ratio_pct = float(confidence.get("ratio_below") or 0) * 100
    logger.info(
        "Telegram %s bloqué (conf %.1f < %.1f) user=%s",
        command_name, score, threshold, user.id,
    )
    await _safe_send(
        chat_id,
        t("low_confidence_block", ui_loc, score=score, threshold=threshold, ratio=ratio_pct),
    )
    return True


async def _handle_refaire_command(user: User, chat_id: str, ui_loc: str) -> None:
    # Garde-fou confiance : on bloque AVANT d'annoncer le "refaire started", sinon l'user croit
    # qu'on régénère pendant que le bloqueur s'affiche après.
    if await _enforce_min_confidence_or_block(user, chat_id, ui_loc, "/refaire"):
        return
    await _safe_send(chat_id, t("refaire_started", ui_loc))
    await _handle_pdf_command(user, chat_id, ui_loc, force_regen=True)


async def _handle_pdf_command(user: User, chat_id: str, ui_loc: str, *, force_regen: bool = False) -> None:
    # Garde-fou : si confiance globale < seuil, on bloque pour éviter de générer une fiche d'hallucinations.
    if await _enforce_min_confidence_or_block(user, chat_id, ui_loc, "/pdf"):
        return
    db = SessionLocal()
    try:
        job = _last_done_job(db, user.id)
        if job is None:
            await _safe_send(chat_id, t("pdf_no_transcript", ui_loc))
            return
        job_id = job.id
        public_id = job.public_id
        subject = job.subject or "General"
        existing_md = job.lesson_markdown
    finally:
        db.close()

    if existing_md and not force_regen:
        await _safe_send(chat_id, t("pdf_lesson_cached", ui_loc))
        lesson_md = existing_md
    else:
        await _safe_send(chat_id, t("pdf_generating", ui_loc))
        db2 = SessionLocal()
        try:
            j = db2.get(TranscriptionJob, job_id)
            if j is None:
                return
            lesson_md, build_err = await lesson_pipeline.build_lesson_for_job(j)
        finally:
            db2.close()
        if not lesson_md or not lesson_md.strip():
            reason = build_err or "Erreur inconnue lors de la génération."
            await _safe_send(chat_id, t("generate_failed_with_reason", ui_loc, reason=reason))
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

    await _send_lesson_pdf(chat_id, ui_loc, public_id, subject, lesson_md)


async def _ensure_lesson_or_generate(user: User, chat_id: str, ui_loc: str) -> Optional[str]:
    db = SessionLocal()
    try:
        job = _last_done_job(db, user.id)
        if job is None:
            return None
        if job.lesson_markdown:
            return job.lesson_markdown
        job_id = job.id
    finally:
        db.close()

    await _safe_send(chat_id, t("quiz_generating_lesson", ui_loc))
    db2 = SessionLocal()
    try:
        j = db2.get(TranscriptionJob, job_id)
        if j is None:
            return None
        lesson_md, build_err = await lesson_pipeline.build_lesson_for_job(j)
    finally:
        db2.close()
    if not lesson_md or not lesson_md.strip():
        reason = build_err or "Erreur inconnue lors de la génération."
        await _safe_send(chat_id, t("generate_failed_with_reason", ui_loc, reason=reason))
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


async def _send_lesson_pdf(chat_id: str, ui_loc: str, public_id: str, subject: str, lesson_md: str) -> None:
    """Construit le PDF (Elite si dispo, sinon ReportLab simple) et l'envoie au chat. Facture par page."""
    pdf_bytes: Optional[bytes] = None
    page_count = 1
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    # Réutilise le flag WhatsApp pour ne pas multiplier les env vars (même infra LaTeX côté VPS).
    elite_enabled = os.getenv("WHATSAPP_PDF_ELITE_ENABLED", "true").lower() in ("1", "true", "yes", "on")

    if api_key and elite_enabled:
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
            logger.info("PDF Elite Telegram public_id=%s pages=%d", public_id, page_count)
        except (PremiumPdfUnavailable, PremiumPdfError) as exc:
            logger.warning("PDF Elite indisponible Telegram (%s) — fallback ReportLab", exc)
            pdf_bytes = None
        except Exception:
            logger.exception("PDF Elite exception Telegram — fallback ReportLab")
            pdf_bytes = None

    if pdf_bytes is None:
        try:
            pdf_bytes, page_count = await asyncio.to_thread(
                lesson_pipeline.build_lesson_pdf_bytes, lesson_md, subject or "General", ui_loc
            )
        except Exception as exc:
            logger.exception("build_lesson_pdf_bytes Telegram échoué public_id=%s", public_id)
            reason = f"PDF builder : {type(exc).__name__} — {str(exc)[:160]}"
            await _safe_send(chat_id, t("send_pdf_failed_with_reason", ui_loc, reason=reason))
            return

    try:
        filename = f"LecturAI_{(subject or 'cours').replace(' ', '_')[:40]}.pdf"
        await tg_client.send_document_bytes(
            chat_id, pdf_bytes, filename=filename,
            caption=t("send_failed_caption", ui_loc),
        )
    except Exception as exc:
        logger.exception("Envoi PDF Telegram échoué public_id=%s", public_id)
        reason = f"Telegram API : {type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(chat_id, t("send_pdf_failed_with_reason", ui_loc, reason=reason))
        return

    # Facturation par page — même tarif que WhatsApp (WHATSAPP_PDF_MRU_PER_PAGE).
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

        await _safe_send(chat_id, t("pdf_billed_pages", ui_loc, pages=page_count, mru=f"{mru_pdf:.2f}"))
    except Exception:
        logger.exception("Débit export PDF Telegram échoué public_id=%s", public_id)


async def _handle_texte_command(user: User, chat_id: str, ui_loc: str) -> None:
    """Envoie la transcription brute du dernier job en .txt (gratuit)."""
    import json as _json

    db = SessionLocal()
    try:
        job = _last_done_job(db, user.id)
        result_json = job.result_json if job else None
        subject = (job.subject or "cours") if job else "cours"
    finally:
        db.close()

    if not result_json:
        await _safe_send(chat_id, t("texte_no_job", ui_loc))
        return
    try:
        payload = _json.loads(result_json)
    except Exception:
        payload = {}
    transcript = (payload.get("transcript") or payload.get("timestamped_transcript") or "").strip()
    if not transcript:
        await _safe_send(chat_id, t("texte_no_job", ui_loc))
        return

    try:
        filename = f"LecturAI_{subject.replace(' ', '_')[:40]}_transcription.txt"
        await tg_client.send_document_bytes(
            chat_id, transcript.encode("utf-8"), filename=filename,
            caption=t("texte_caption", ui_loc), mime="text/plain",
        )
        await _safe_send(chat_id, t("texte_sent", ui_loc))
    except Exception as exc:
        logger.exception("Envoi .txt Telegram échoué chat=%s", chat_id)
        reason = f"Telegram API : {type(exc).__name__} — {str(exc)[:160]}"
        await _safe_send(chat_id, t("send_pdf_failed_with_reason", ui_loc, reason=reason))


# =============================================================================
# Quiz interactif (InlineKeyboardButton)
# =============================================================================

async def _handle_quiz_command(user: User, chat_id: str, ui_loc: str) -> None:
    # Quiz dépend de la fiche → même garde-fou que /pdf : pas de génération sur audio non fiable.
    if await _enforce_min_confidence_or_block(user, chat_id, ui_loc, "/quiz"):
        return
    lesson_md = await _ensure_lesson_or_generate(user, chat_id, ui_loc)
    if lesson_md is None:
        await _safe_send(chat_id, t("quiz_none", ui_loc))
        return

    db = SessionLocal()
    try:
        job = _last_done_job(db, user.id)
        job_public_id = job.public_id if job else None
    finally:
        db.close()

    questions = lesson_pipeline.extract_quiz(lesson_md)
    if not questions:
        await _safe_send(chat_id, t("quiz_none", ui_loc))
        return

    # Débit symbolique pour démarrer une session quiz (même tarif que WA).
    from credits_wallet import debit_credits
    from pricing import billed_mru_to_wallet_units_debit, WHATSAPP_QUIZ_BILLED_MRU

    quiz_units = billed_mru_to_wallet_units_debit(WHATSAPP_QUIZ_BILLED_MRU)
    if quiz_units > 0:
        db_q = SessionLocal()
        try:
            u = db_q.get(User, user.id)
            if u is None:
                return
            if int(u.credit_balance or 0) < quiz_units:
                topup = tg_config.topup_url()
                if topup:
                    await _safe_send(chat_id, t("wallet_blocked", ui_loc, topup_url=topup))
                else:
                    await _safe_send(chat_id, t("wallet_blocked_no_url", ui_loc))
                return
            try:
                debit_credits(db_q, u, quiz_units)
            except Exception:
                logger.exception("Débit quiz Telegram échoué chat=%s", chat_id)
                return
        finally:
            db_q.close()

    _quiz_sessions[chat_id] = {
        "questions": questions,
        "idx": 0,
        "score": 0,
        "ui_loc": ui_loc,
        "job_public_id": job_public_id,
    }
    await _safe_send(chat_id, t("quiz_intro", ui_loc, n=len(questions)))
    await _send_quiz_question(chat_id)


async def _send_quiz_question(chat_id: str) -> None:
    sess = _quiz_sessions.get(chat_id)
    if not sess:
        return
    questions = sess["questions"]
    idx = sess["idx"]
    if idx >= len(questions):
        ui_loc = sess["ui_loc"]
        score = sess["score"]
        total = len(questions)
        _quiz_sessions.pop(chat_id, None)
        await _safe_send(chat_id, t("quiz_done", ui_loc, score=score, total=total))
        return

    q = questions[idx]
    ui_loc = sess["ui_loc"]
    header_line = t("quiz_question", ui_loc, idx=idx + 1, total=len(questions), question=q["question"])
    options_text = "\n".join(f"*{let})* {txt}" for let, txt in q["options"])
    full_text = f"{header_line}\n\n{options_text}"

    # Boutons inline : 1 par ligne (mieux pour mobile, libellés peuvent être longs).
    buttons = [[(f"{letter})", f"quiz:{idx}:{letter}")] for letter, _ in q["options"]]
    try:
        await tg_client.send_inline_keyboard(chat_id, full_text, buttons)
    except Exception:
        logger.exception("send_inline_keyboard quiz échoué — fallback texte")
        await _safe_send(chat_id, full_text)


async def _handle_quiz_reply(chat_id: str, callback_data: str) -> None:
    """Callback data attendue : 'quiz:<idx>:<letter>'."""
    sess = _quiz_sessions.get(chat_id)
    if not sess:
        return
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "quiz":
        return
    try:
        idx = int(parts[1])
    except ValueError:
        return
    letter = parts[2].upper()
    if idx != sess["idx"]:
        return  # clic sur une question périmée (l'user a cliqué 2 fois)

    q = sess["questions"][idx]
    ui_loc = sess["ui_loc"]
    correct = q["correct"]
    explanation = q["explanation"] or ""

    if letter == correct:
        sess["score"] += 1
        await _safe_send(chat_id, t("quiz_correct", ui_loc, explanation=explanation))
    else:
        await _safe_send(chat_id, t("quiz_wrong", ui_loc, correct=correct, explanation=explanation))

    sess["idx"] += 1
    await _send_quiz_question(chat_id)


# =============================================================================
# Partage public
# =============================================================================

async def _handle_confiance_command(user: User, chat_id: str, ui_loc: str) -> None:
    """Affiche le rapport de qualité du dernier job.

    Source préférée : ``inaudible_summary`` (produit par le segment cleaner, aligné sur la
    décision de blocage /pdf — durée exploitable + liste des passages flaggés inaudibles).

    Fallback (jobs antérieurs au segment cleaner) : ancien ``confidence_summary`` (score 0-100
    + passages sous seuil 90%). Moins clair mais conservé pour compatibilité.
    """
    import json as _json

    db = SessionLocal()
    try:
        job = _last_done_job(db, user.id)
        if job is None:
            await _safe_send(chat_id, t("pdf_no_transcript", ui_loc))
            return
        result_json = job.result_json
    finally:
        db.close()

    if not result_json:
        await _safe_send(chat_id, t("pdf_no_transcript", ui_loc))
        return
    try:
        payload = _json.loads(result_json)
    except Exception:
        await _safe_send(chat_id, t("pdf_no_transcript", ui_loc))
        return

    # === Préféré : inaudible_summary (nouvelle source unique de vérité, aligné avec /pdf) ===
    inaudible = payload.get("inaudible_summary") if isinstance(payload, dict) else None
    if isinstance(inaudible, dict) and inaudible.get("total_duration_sec") is not None:
        from asr_segment_cleaner import format_inaudible_summary_for_telegram
        await _safe_send(
            chat_id,
            format_inaudible_summary_for_telegram(inaudible, ui_locale=ui_loc),
        )
        return

    # === Fallback (jobs antérieurs) : ancien confidence_summary ===
    summary = payload.get("confidence_summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or summary.get("overall_score_0_100") is None:
        try:
            from asr_confidence import compute_overall
            passages = payload.get("asr_passages_annotated") if isinstance(payload, dict) else []
            summary = compute_overall(passages or [])
        except Exception:
            logger.exception("Recalcul confidence_summary échoué chat=%s", chat_id)
            summary = None

    if not summary or summary.get("overall_score_0_100") is None:
        await _safe_send(
            chat_id,
            "ℹ️ Pas de données de qualité disponibles pour ce cours."
            if not ui_loc.startswith("ar")
            else "ℹ️ لا توجد بيانات جودة لهذا الدرس.",
        )
        return

    from asr_confidence import format_summary_for_telegram
    await _safe_send(chat_id, format_summary_for_telegram(summary, ui_locale=ui_loc))


async def _handle_partage_command(user: User, chat_id: str, ui_loc: str) -> None:
    # /partage publie une URL pointant sur la fiche — pas honnête de partager du contenu halluciné.
    if await _enforce_min_confidence_or_block(user, chat_id, ui_loc, "/partage"):
        return
    lesson_md = await _ensure_lesson_or_generate(user, chat_id, ui_loc)
    if lesson_md is None:
        await _safe_send(chat_id, t("share_no_lesson", ui_loc))
        return

    db = SessionLocal()
    try:
        job = _last_done_job(db, user.id)
        if job is None:
            await _safe_send(chat_id, t("share_no_lesson", ui_loc))
            return
        public_id = job.public_id
    finally:
        db.close()

    # On réutilise tel quel la fonction WA (génère le token + débite + retourne URL).
    from whatsapp.processor import _ensure_share_url

    share_url, share_billed = _ensure_share_url(public_id)
    if not share_url:
        topup = tg_config.topup_url()
        if topup:
            await _safe_send(chat_id, t("wallet_blocked", ui_loc, topup_url=topup))
        else:
            await _safe_send(chat_id, t("wallet_blocked_no_url", ui_loc))
        return
    await _safe_send(chat_id, t("share_link", ui_loc, url=share_url))
    if share_billed > 0:
        await _safe_send(chat_id, t("share_billed", ui_loc, mru=f"{share_billed:.2f}"))
