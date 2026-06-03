# app/services/checkin_sender.py
"""Deliver a proactive wellness check-in to a single patient. Idempotent and interval-aware."""

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.patient import Patient
from app.services.checkin_generator import generate_checkin, _current_week, _MILESTONE_WEEKS
from app.services.message_store import save_outbound
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)

# Minimum time between check-ins per risk level.
# The window is slightly shorter than the nominal period to absorb minor
# scheduling drift (e.g. 20 h for "daily" so a cron at 08:00 UTC never skips).
_RISK_INTERVALS: dict[str, timedelta] = {
    "high":   timedelta(hours=20),          # daily
    "medium": timedelta(days=6, hours=12),  # weekly
    "low":    timedelta(days=13),           # fortnightly
}


def _last_checkin_sent_at(patient_id, db: Session) -> datetime | None:
    """Return the created_at timestamp of the most recent check-in for this patient, or None."""
    msg = (
        db.query(Message)
        .filter(
            Message.patient_id == patient_id,
            Message.message_type == "checkin",
            Message.direction == "out",
        )
        .order_by(Message.created_at.desc())
        .first()
    )
    return msg.created_at if msg else None


def _is_checkin_due(patient: Patient, db: Session) -> bool:
    """
    Return True if this patient is due for a proactive check-in right now.

    Logic:
    1. Has a risk-level interval elapsed since the last check-in?
    2. For low-risk patients only: are they at a milestone gestational week
       (12, 20, 28, 36) AND no check-in was sent in the last 7 days?
       If yes, send a milestone check-in even if the fortnightly interval
       hasn't elapsed yet.
    """
    risk = patient.risk_level or "medium"
    interval = _RISK_INTERVALS.get(risk, _RISK_INTERVALS["medium"])

    last_sent = _last_checkin_sent_at(patient.id, db)

    # Never been sent → always due
    if last_sent is None:
        return True

    # Ensure timezone-aware comparison
    if last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)

    if now - last_sent >= interval:
        return True

    # Low-risk milestone override
    if risk == "low" and patient.status != "post_loss":
        week = _current_week(patient)
        if week in _MILESTONE_WEEKS:
            seven_days_ago = now - timedelta(days=7)
            if last_sent < seven_days_ago:
                return True

    return False


def _is_silenced(patient: Patient) -> bool:
    if patient.opt_out_status == "stopped":
        return True
    if patient.opt_out_status == "paused" and patient.paused_until:
        return patient.paused_until > datetime.now(timezone.utc)
    return False


def send_checkin(patient: Patient, db: Session) -> bool:
    """Generate and deliver a wellness check-in for one patient.

    Returns True if a check-in was sent, False if skipped for any reason.
    Raises RuntimeError if the SMS provider rejects the message (caller
    should rollback and log).
    """
    if _is_silenced(patient):
        logger.info("Check-in skipped (silenced) | patient=%s", patient.id)
        return False

    if patient.pending_loss_confirmation:
        logger.info("Check-in skipped (pending loss confirmation) | patient=%s", patient.id)
        return False

    if not _is_checkin_due(patient, db):
        logger.debug("Check-in not due yet | patient=%s | risk=%s", patient.id, patient.risk_level)
        return False

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

    db.add(msg)
    db.commit()
    logger.info(
        "Check-in sent | patient=%s | channel=%s | risk=%s",
        patient.id, msg.channel, patient.risk_level,
    )
    return True
