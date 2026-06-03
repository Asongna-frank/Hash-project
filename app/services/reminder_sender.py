# app/services/reminder_sender.py
"""
Composes appointment reminders and delivers them on the right channel.

Routing (hard rule 17):
  - choronko   -> SMS via sms_service (Queen SMS), SMS-safe short form,
                  then logged to messages with channel="sms"
  - smartphone -> in-app message (channel="app", message_type="reminder",
                  is_read=False) which the bell/banner polls

Each reminder fires exactly once (24h, 2h), guarded by reminder_24h_sent /
reminder_2h_sent on the appointment.
"""

import logging

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.services.message_store import save_outbound
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)


# ---- message composition ------------------------------------------------

def _compose_24h_full(name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    return (
        f"Hi {name}, just a reminder that you have an appointment tomorrow — "
        f"{appt.title} — on {dt.strftime('%A, %B %d')} at {dt.strftime('%I:%M %p')}. "
        f"Please prepare and have your documents ready."
    )


def _compose_24h_sms(name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    return (
        f"HASH: Hi {name}, reminder: {appt.title} tomorrow "
        f"{dt.strftime('%d %b')} at {dt.strftime('%I:%M %p')}. Please prepare."
    )


def _compose_2h_full(name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    return (
        f"Hi {name}, your appointment — {appt.title} — is in about 2 hours at "
        f"{dt.strftime('%I:%M %p')}. Please start making your way to the hospital."
    )


def _compose_2h_sms(name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    return (
        f"HASH: Hi {name}, {appt.title} is in ~2 hours at "
        f"{dt.strftime('%I:%M %p')}. Please head to the hospital."
    )


# ---- delivery -----------------------------------------------------------

def _deliver(appt: Appointment, full_text: str, sms_text: str, db: Session) -> None:
    """Route one reminder by the patient's account_type."""
    patient = appt.patient

    if patient.account_type == "choronko":
        # SMS path — send first; only record on success
        result = sms_service.send_sms(to=patient.phone, message=sms_text)
        if not result.ok:
            # Do NOT set the *_sent flag — next scheduler pass retries in-window
            raise RuntimeError(f"SMS send failed: {result.error}")
        # Log the SMS in message history so a clinician sees it on the dashboard
        out_msg = save_outbound(
            patient_id=patient.id,
            content=sms_text,
            channel="sms",
            message_type="reminder",
        )
        db.add(out_msg)
    else:
        # smartphone -> in-app message + unread poll flag
        out_msg = save_outbound(
            patient_id=patient.id,
            content=full_text,
            channel="app",
            message_type="reminder",
        )
        # is_read defaults to False; set explicitly for clarity
        out_msg.is_read = False
        db.add(out_msg)


def send_24h_reminder(appt: Appointment, db: Session) -> None:
    name = appt.patient.name
    _deliver(appt, _compose_24h_full(name, appt), _compose_24h_sms(name, appt), db)
    appt.reminder_24h_sent = True
    db.commit()
    logger.info("24h reminder sent | appt=%s | patient=%s", appt.id, appt.patient_id)


def send_2h_reminder(appt: Appointment, db: Session) -> None:
    name = appt.patient.name
    _deliver(appt, _compose_2h_full(name, appt), _compose_2h_sms(name, appt), db)
    appt.reminder_2h_sent = True
    db.commit()
    logger.info("2h reminder sent | appt=%s | patient=%s", appt.id, appt.patient_id)
