# app/services/checkin_sender.py
"""
Deliver a proactive wellness check-in to a single patient.

Cadence is risk-driven and read from CHECK_IN_CADENCE_DAYS in risk_config.py:
  high   → every 3 days
  medium → every 7 days
  low    → every 14 days (+ existing milestone-week override)

Missed check-ins (no patient reply before the next check-in fires) are tracked
per patient. When the count reaches the MISSED_CHECKIN_ESCALATION threshold for
the patient's risk level, missed_checkin_flag is set to True so the clinician
dashboard can surface it. The flag is set only once per threshold-crossing and
reset when the patient next replies.
"""

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.patient import Patient
from app.services.checkin_generator import generate_checkin, _current_week, _MILESTONE_WEEKS
from app.services.message_store import save_outbound
from app.services.push_service import push_service
from app.services.sms_service import sms_service
from app.core.risk_config import CHECK_IN_CADENCE_DAYS, MISSED_CHECKIN_ESCALATION

logger = logging.getLogger(__name__)

# Build timedelta map from config — no hardcoded numbers anywhere else.
_RISK_INTERVALS: dict[str, timedelta] = {
    risk: timedelta(days=days)
    for risk, days in CHECK_IN_CADENCE_DAYS.items()
}


# ── interval helpers ──────────────────────────────────────────────────────────

def _last_checkin_sent_at(patient_id, db: Session) -> datetime | None:
    msg = (
        db.query(Message)
        .filter(
            Message.patient_id   == patient_id,
            Message.message_type == "checkin",
            Message.direction    == "out",
        )
        .order_by(Message.created_at.desc())
        .first()
    )
    return msg.created_at if msg else None


def _is_checkin_due(patient: Patient, db: Session) -> bool:
    """
    Return True when a proactive check-in should fire for this patient.

    Rules (in order):
    1. Never sent → always due.
    2. Standard risk interval elapsed → due.
    3. Low-risk milestone week (12, 20, 28, 36): due if last check-in > 7 days ago,
       even while still inside the 14-day fortnightly window.
    """
    risk     = patient.risk_level or "medium"
    interval = _RISK_INTERVALS.get(risk, _RISK_INTERVALS["medium"])

    last_sent = _last_checkin_sent_at(patient.id, db)

    if last_sent is None:
        return True

    if last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)

    if now - last_sent >= interval:
        return True

    # Low-risk milestone override (gestational weeks 12, 20, 28, 36)
    if risk == "low" and patient.status != "post_loss":
        week = _current_week(patient)
        if week in _MILESTONE_WEEKS:
            seven_days_ago = now - timedelta(days=7)
            if last_sent < seven_days_ago:
                return True

    return False


# ── silence check ─────────────────────────────────────────────────────────────

def _is_silenced(patient: Patient) -> bool:
    if patient.opt_out_status == "stopped":
        return True
    if patient.opt_out_status == "paused" and patient.paused_until:
        return patient.paused_until > datetime.now(timezone.utc)
    return False


# ── missed check-in tracking ─────────────────────────────────────────────────

def _has_replied_since(patient_id, since: datetime, db: Session) -> bool:
    """Return True if the patient sent any inbound message after `since`."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return (
        db.query(Message)
        .filter(
            Message.patient_id == patient_id,
            Message.direction  == "in",
            Message.created_at >= since,
        )
        .first()
    ) is not None


def _update_missed_counter(patient: Patient, db: Session) -> None:
    """
    Before sending a new check-in, determine whether the previous one was
    answered. Updates consecutive_missed_checkins and missed_checkin_flag on
    the patient object (caller must commit).

    A check-in is "answered" if at least one inbound message arrived after it
    was sent. If not answered, the counter increments. When the counter reaches
    the MISSED_CHECKIN_ESCALATION threshold for this patient's risk level,
    missed_checkin_flag is set to True exactly once per threshold-crossing.
    Any inbound reply resets counter to 0 and clears the flag.
    """
    last_sent_at = _last_checkin_sent_at(patient.id, db)
    if last_sent_at is None:
        return  # first ever check-in — nothing to miss

    if _has_replied_since(patient.id, last_sent_at, db):
        # Patient replied since the last check-in → reset
        if patient.consecutive_missed_checkins != 0 or patient.missed_checkin_flag:
            patient.consecutive_missed_checkins = 0
            patient.missed_checkin_flag = False
    else:
        # No reply → previous check-in was missed
        patient.consecutive_missed_checkins = (patient.consecutive_missed_checkins or 0) + 1

        threshold = MISSED_CHECKIN_ESCALATION.get(patient.risk_level or "medium")
        if (
            threshold is not None
            and patient.consecutive_missed_checkins >= threshold
            and not patient.missed_checkin_flag
        ):
            patient.missed_checkin_flag = True
            logger.warning(
                "Missed check-in escalation flagged | patient=%s | risk=%s | missed=%d",
                patient.id,
                patient.risk_level,
                patient.consecutive_missed_checkins,
            )


# ── public entry point ────────────────────────────────────────────────────────

def send_checkin(patient: Patient, db: Session) -> bool:
    """
    Generate and deliver a wellness check-in for one patient.

    Returns True if a check-in was sent, False if skipped for any reason.
    Raises RuntimeError if the SMS provider rejects the message — the caller
    (scheduler) should rollback and log.
    """
    if _is_silenced(patient):
        logger.debug("Check-in skipped (silenced) | patient=%s", patient.id)
        return False

    if patient.pending_loss_confirmation:
        logger.debug(
            "Check-in skipped (pending loss confirmation) | patient=%s", patient.id
        )
        return False

    if not _is_checkin_due(patient, db):
        logger.debug(
            "Check-in not due | patient=%s | risk=%s", patient.id, patient.risk_level
        )
        return False

    # Update missed-response counter before sending the new check-in.
    _update_missed_counter(patient, db)

    checkin_text = generate_checkin(patient)

    if patient.account_type == "choronko":
        result = sms_service.send_sms(to=patient.phone, message=checkin_text)
        if not result.ok:
            raise RuntimeError(
                f"Queen SMS rejected check-in for patient {patient.id}: {result.error}"
            )
        msg = save_outbound(patient.id, checkin_text, channel="sms", message_type="checkin")
    else:
        msg = save_outbound(patient.id, checkin_text, channel="app", message_type="checkin")
        result = push_service.send_push(
            patient_uuid=str(patient.id),
            title="HASH — Wellness Check-in",
            message=checkin_text,
        )
        if not result.ok:
            logger.warning(
                "Push notification failed for check-in | patient=%s | %s",
                patient.id, result.error,
            )

    db.add(patient)  # persist counter/flag changes
    db.add(msg)
    db.commit()
    logger.info(
        "Check-in sent | patient=%s | channel=%s | risk=%s",
        patient.id, msg.channel, patient.risk_level,
    )
    return True
