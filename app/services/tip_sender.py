# app/services/tip_sender.py
"""Deliver the daily tip for a single patient. Idempotent — safe to call multiple times."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.patient import Patient
from app.services.message_store import save_outbound
from app.services.sms_service import sms_service
from app.services.tip_generator import generate_daily_tip

logger = logging.getLogger(__name__)


def _already_sent_today(patient_id, db: Session) -> bool:
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        db.query(Message)
        .filter(
            Message.patient_id == patient_id,
            Message.message_type == "tip",
            Message.direction == "out",
            Message.created_at >= today_start,
        )
        .first()
        is not None
    )


def _is_silenced(patient: Patient) -> bool:
    if patient.opt_out_status == "stopped":
        return True
    if patient.opt_out_status == "paused" and patient.paused_until:
        return patient.paused_until > datetime.now(timezone.utc)
    return False


def send_daily_tip(patient: Patient, db: Session) -> None:
    """Generate and deliver today's tip for one patient.

    Skips silenced patients, patients mid-loss-confirmation, and patients
    who already received a tip today. Raises RuntimeError if the SMS provider
    rejects the message (caller should rollback and log).
    """
    if _is_silenced(patient):
        logger.info("Daily tip skipped (silenced) | patient=%s", patient.id)
        return

    if patient.pending_loss_confirmation:
        logger.info("Daily tip skipped (pending loss confirmation) | patient=%s", patient.id)
        return

    if _already_sent_today(patient.id, db):
        logger.info("Daily tip skipped (already sent today) | patient=%s", patient.id)
        return

    tip_text = generate_daily_tip(patient)

    if patient.account_type == "choronko":
        result = sms_service.send_sms(to=patient.phone, message=tip_text)
        if not result.ok:
            raise RuntimeError(
                f"Queen SMS rejected daily tip for patient {patient.id}: {result.error}"
            )
        msg = save_outbound(patient.id, tip_text, channel="sms", message_type="tip")
    else:
        msg = save_outbound(patient.id, tip_text, channel="app", message_type="tip")

    db.add(msg)
    db.commit()
    logger.info("Daily tip sent | patient=%s | channel=%s", patient.id, msg.channel)
