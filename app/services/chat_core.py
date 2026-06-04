# app/services/chat_core.py
"""
Channel-agnostic chat brain.

Every channel (app HTTP, inbound SMS, future WhatsApp) calls process_message().
The brain never knows or cares about transport mechanics — it receives an
already-resolved Patient + text + channel and returns a ChatReply. There is
exactly ONE path here: translate, store, detect loss, triage, reply, alert.
Duplicating this per channel is forbidden — it causes app-vs-SMS care drift,
which violates the choronko-parity hard rule (#15).

Pipeline (in order), all on the ENGLISH pivot of the text:
  0. PAUSE / STOP / RESUME pre-filter (both channels, before translation)
  1. Pivot IN: patient.language -> English (fail-safe: keep original + flag)
  2. Save inbound (English content + source_lang + provider_message_id)
  3. Post-loss track (runs the engine in post-loss mode)
  4. Pending loss-confirmation handler (yes/no from a prior AMBIGUOUS)
  5. Loss detection (keyword -> LLM); CONFIRMED is the CRISIS carve-out
  6. Normal conversation + triage
  7. Pivot OUT: English -> patient.language (crisis/static content is NOT
     live-translated — it is served pre-approved from content_store)
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.core.risk_config import RUBRIC_VERSION
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.services import content_store
from app.services.conversation import generate_reply
from app.services.loss_detection import detect_loss
from app.services.message_store import save_inbound, save_outbound
from app.services.translation_service import translation_service

logger = logging.getLogger(__name__)

# Opt-out control words — channel-agnostic, matched before translation.
OPT_OUT_KEYWORDS = {
    "pause": "paused",
    "stop": "stopped",
    "resume": "resume",
}

_AFFIRMATIVE = {"yes", "yeah", "yep", "oui", "sim", "yes i did", "yes i have"}
_NEGATIVE = {"no", "non", "nope", "nao", "não", "no i didn't", "no i haven't"}


@dataclass
class ChatReply:
    text: str                      # reply already in the patient's language
    channel: str                   # echoes the inbound channel — caller replies via this
    is_crisis: bool = False        # True only for the pre-approved stored crisis message
    triage_level: str | None = None
    loss_detected: bool = False    # kept for the app API contract / post-loss UI


# ── helpers ───────────────────────────────────────────────────────────────────

def _patient_lang(patient: Patient) -> str:
    return (getattr(patient, "language", None) or "en").lower()


def _get_pregnancy(patient_id, db: Session) -> Pregnancy | None:
    return (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient_id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )


def _pivot_in(patient: Patient, raw_text: str) -> tuple[str, str, bool]:
    """
    Translate inbound text to English. Returns (english_text, source_lang,
    translation_failed). Fails SAFE: on error keep the original text (so the
    message is never dropped) and flag it for clinician review.
    """
    lang = _patient_lang(patient)
    if lang == "en":
        return raw_text, "en", False
    try:
        english = translation_service.translate(raw_text, lang, "en")
        return english, lang, False
    except Exception as exc:  # noqa: BLE001 — fail safe, never drop the message
        logger.error(
            "Translate-IN failed (patient=%s, lang=%s) — running triage on original, flagging: %s",
            patient.id, lang, exc,
        )
        return raw_text, lang, True


def _pivot_out(patient: Patient, english_text: str) -> str:
    """Translate an English reply to the patient's language. Fail-safe: send English."""
    lang = _patient_lang(patient)
    if lang == "en":
        return english_text
    try:
        return translation_service.translate(english_text, "en", lang)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Translate-OUT failed (patient=%s, lang=%s) — sending English: %s",
            patient.id, lang, exc,
        )
        return english_text


def _alert_hospital(patient: Patient, db: Session, reason: str) -> None:
    """
    M6 hospital-alert hook. Real-time dashboard alerting is implemented in M6;
    for now this records the alert so the wiring point is explicit and testable.
    """
    logger.warning("HOSPITAL ALERT | patient=%s | reason=%s", patient.id, reason)
    # TODO M6: push a real-time alert to the hospital dashboard.


def _handle_opt_out(patient: Patient, keyword: str, channel: str, db: Session) -> ChatReply:
    """PAUSE / STOP / RESUME — one confirmation, no further nudges. Both channels."""
    if keyword == "pause":
        patient.opt_out_status = "paused"
        patient.paused_until = datetime.now(timezone.utc) + timedelta(days=7)
        content_key = "pause_confirmation"
    elif keyword == "stop":
        patient.opt_out_status = "stopped"
        patient.paused_until = None
        content_key = "stop_confirmation"
    else:  # resume
        patient.opt_out_status = None
        patient.paused_until = None
        content_key = "resume_confirmation"
    db.commit()

    reply = content_store.get_content(content_key, _patient_lang(patient))
    out_msg = save_outbound(patient.id, reply, channel=channel, source_lang=_patient_lang(patient))
    db.add(out_msg)
    db.commit()
    return ChatReply(text=reply, channel=channel, triage_level="low")


def _handle_loss_confirmed(patient: Patient, pregnancy: Pregnancy | None,
                           channel: str, db: Session) -> ChatReply:
    """
    CRISIS carve-out. Apply post-loss state, raise the hospital alert, and reply
    with the PRE-APPROVED stored crisis message in the patient's language
    (never live-translated). Returns is_crisis=True.
    """
    current_ga_weeks = (date.today() - patient.lmp).days // 7

    patient.status = "post_loss"
    patient.pending_loss_confirmation = False

    if pregnancy:
        pregnancy.outcome = "loss"
        pregnancy.loss_date = date.today()
        pregnancy.ga_at_loss = current_ga_weeks
        pregnancy.routine_paused = True

    if patient.risk_level != "high":
        patient.risk_level = "high"
        patient.risk_level_set_at = datetime.now(timezone.utc)
        patient.risk_level_set_by = "system"
        db.add(RiskAssessment(
            patient_id=patient.id,
            computed_by="system",
            inputs={
                "reason": "Automatic escalation on confirmed pregnancy loss",
                "loss_confirmed": True,
            },
            rubric_version=RUBRIC_VERSION,
            result_level="high",
            score=None,
        ))

    db.commit()
    logger.info("Pregnancy loss confirmed | patient=%s | status -> post_loss", patient.id)

    _alert_hospital(patient, db, reason="confirmed pregnancy loss")

    # Pre-approved, stored per language — NOT live-translated.
    reply = content_store.get_content("post_loss_opening", _patient_lang(patient))
    out_msg = save_outbound(patient.id, reply, channel=channel,
                            message_type="crisis", source_lang=_patient_lang(patient))
    db.add(out_msg)
    db.commit()

    return ChatReply(text=reply, channel=channel, is_crisis=True,
                     triage_level="high", loss_detected=True)


def _ambiguous_reply(patient: Patient, in_msg, channel: str, db: Session) -> ChatReply:
    """Sensitive loss follow-up — served from the pre-approved store, not translated."""
    reply = content_store.get_content("loss_ambiguous_followup", _patient_lang(patient))
    if in_msg is not None:
        in_msg.triage_level = "medium"
        db.add(in_msg)
    out_msg = save_outbound(patient.id, reply, channel=channel, source_lang=_patient_lang(patient))
    db.add(out_msg)
    db.commit()
    return ChatReply(text=reply, channel=channel, triage_level="medium")


# ── public entry point ────────────────────────────────────────────────────────

def process_message(
    patient: Patient,
    text: str,
    channel: str,
    db: Session,
    provider_message_id: str | None = None,
) -> ChatReply:
    """
    The single brain entry point. `patient` is already resolved by the transport
    layer (JWT for app, phone lookup for SMS). `channel` is "app" | "sms".
    `provider_message_id` (SMS only) is stored on the inbound row for idempotency.
    """
    raw = (text or "").strip()
    lower = raw.lower()

    # STEP 0 — opt-out pre-filter (before translation; control words are language-agnostic)
    if lower in OPT_OUT_KEYWORDS:
        in_msg = save_inbound(
            patient.id, raw, triage_level="low", channel=channel,
            source_lang=_patient_lang(patient), provider_message_id=provider_message_id,
        )
        db.add(in_msg)
        db.commit()
        return _handle_opt_out(patient, lower, channel, db)

    # STEP 1 — pivot IN (fail-safe)
    english_text, source_lang, translation_failed = _pivot_in(patient, raw)

    # STEP 2 — save inbound (English content + source_lang + provider id + review flag)
    in_msg = save_inbound(
        patient.id, english_text, triage_level="low", channel=channel,
        source_lang=source_lang, provider_message_id=provider_message_id,
        flagged_for_review=translation_failed,
    )
    db.add(in_msg)
    db.commit()
    db.refresh(in_msg)

    pregnancy = _get_pregnancy(patient.id, db)

    # STEP 3 — post-loss track (same engine, post-loss mode; M9 extends this)
    if patient.status == "post_loss":
        reply_en, triage = generate_reply(patient, english_text, db)
        in_msg.triage_level = triage
        db.add(in_msg)
        reply_local = _pivot_out(patient, reply_en)
        out_msg = save_outbound(patient.id, reply_local, channel=channel,
                                source_lang=_patient_lang(patient))
        db.add(out_msg)
        db.commit()
        return ChatReply(text=reply_local, channel=channel, triage_level=triage,
                         loss_detected=True)

    # STEP 4 — pending loss confirmation from a previous AMBIGUOUS
    if patient.pending_loss_confirmation:
        if any(a in english_text.lower() for a in _AFFIRMATIVE):
            in_msg.triage_level = "high"
            db.add(in_msg)
            db.commit()
            return _handle_loss_confirmed(patient, pregnancy, channel, db)
        elif any(n in english_text.lower() for n in _NEGATIVE):
            patient.pending_loss_confirmation = False
            db.commit()
            # fall through to normal conversation
        else:
            result = detect_loss(english_text)
            if result == "CONFIRMED":
                in_msg.triage_level = "high"
                db.add(in_msg)
                db.commit()
                return _handle_loss_confirmed(patient, pregnancy, channel, db)
            if result == "AMBIGUOUS":
                return _ambiguous_reply(patient, in_msg, channel, db)
            patient.pending_loss_confirmation = False
            db.commit()

    # STEP 5 — loss detection on normal messages
    loss_result = detect_loss(english_text)
    if loss_result == "CONFIRMED":
        in_msg.triage_level = "high"
        db.add(in_msg)
        db.commit()
        return _handle_loss_confirmed(patient, pregnancy, channel, db)
    if loss_result == "AMBIGUOUS":
        patient.pending_loss_confirmation = True
        db.commit()
        return _ambiguous_reply(patient, in_msg, channel, db)

    # STEP 6 — normal conversation + triage (NOT_A_LOSS path)
    reply_en, triage = generate_reply(patient, english_text, db)
    in_msg.triage_level = triage
    db.add(in_msg)

    reply_local = _pivot_out(patient, reply_en)
    out_msg = save_outbound(patient.id, reply_local, channel=channel,
                            source_lang=_patient_lang(patient))
    db.add(out_msg)
    db.commit()

    # High-acuity message → alert hospital (M6). The reply itself is the empathetic
    # translated LLM reply (which already tells danger-sign patients to go to
    # hospital); is_crisis stays False — that flag is reserved for the stored
    # pre-approved crisis message (confirmed loss).
    if triage == "high":
        _alert_hospital(patient, db, reason="high-acuity chat message")

    return ChatReply(text=reply_local, channel=channel, triage_level=triage,
                     loss_detected=False)
