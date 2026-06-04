# app/services/reminder_sender.py
"""
Compose and deliver appointment reminder alarms.

Two alarms per appointment, keyed on reminder_datetime (patient-chosen or
system-computed):
  alarm_1  — fires 30 minutes BEFORE reminder_datetime
  alarm_2  — fires AT reminder_datetime

Channel routing (hard rule 17):
  choronko   → Twilio SMS, SMS-safe short form; also logged to messages table
  smartphone → in-app message (channel="app", message_type="reminder", is_read=False)
               which the notification bell polls

SMS failure raises RuntimeError so the scheduler can catch it per-appointment,
log the error, and NOT set the *_sent flag — allowing a retry on the next pass.
"""

import logging

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.services.message_store import save_outbound
from app.services.push_service import push_service
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)


# ── Message composition ───────────────────────────────────────────────────────

def _compose_alarm1_full(name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    rd = appt.reminder_datetime
    return (
        f"Hi {name}, your reminder for '{appt.title}' is coming up at "
        f"{rd.strftime('%I:%M %p')} today. The appointment itself is at "
        f"{dt.strftime('%I:%M %p')} on {dt.strftime('%A, %B %d')}. "
        f"Please start preparing."
    )


def _compose_alarm1_sms(name: str, appt: Appointment) -> str:
    rd = appt.reminder_datetime
    return (
        f"HASH: Hi {name}, prep reminder for {appt.title} at "
        f"{rd.strftime('%I:%M %p')}. Appt at "
        f"{appt.appointment_datetime.strftime('%I:%M %p %d %b')}."
    )


def _compose_alarm2_full(name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    return (
        f"Hi {name}, this is your reminder for '{appt.title}' — "
        f"your appointment is on {dt.strftime('%A, %B %d')} at {dt.strftime('%I:%M %p')}. "
        f"Please head to the hospital now if you haven't already."
    )


def _compose_alarm2_sms(name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    return (
        f"HASH: Reminder! {appt.title} on "
        f"{dt.strftime('%d %b')} at {dt.strftime('%I:%M %p')}. Head to hospital."
    )


# ── Delivery ─────────────────────────────────────────────────────────────────

def _deliver(appt: Appointment, full_text: str, sms_text: str, db: Session) -> None:
    """Route to the right channel. Raises RuntimeError if SMS fails (no retry guard set)."""
    patient = appt.patient

    if patient.account_type == "choronko":
        result = sms_service.send_sms(to=patient.phone, message=sms_text)
        if not result.ok:
            raise RuntimeError(f"SMS send failed: {result.error}")
        out_msg = save_outbound(
            patient_id=patient.id,
            content=sms_text,
            channel="sms",
            message_type="reminder",
        )
        db.add(out_msg)
    else:
        # smartphone — in-app unread message + push notification
        out_msg = save_outbound(
            patient_id=patient.id,
            content=full_text,
            channel="app",
            message_type="reminder",
        )
        out_msg.is_read = False
        db.add(out_msg)
        result = push_service.send_push(
            patient_uuid=str(patient.id),
            title="HASH — Appointment Reminder",
            message=full_text,
        )
        if not result.ok:
            logger.warning(
                "Push notification failed for reminder | patient=%s | %s",
                patient.id, result.error,
            )


# ── Public entry points ───────────────────────────────────────────────────────

def send_alarm_1(appt: Appointment, db: Session) -> None:
    """30-minute pre-reminder alarm. Sets alarm_1_sent=True on success."""
    name = appt.patient.name
    _deliver(appt, _compose_alarm1_full(name, appt), _compose_alarm1_sms(name, appt), db)
    appt.alarm_1_sent = True
    db.commit()
    logger.info("Alarm 1 sent | appt=%s | patient=%s", appt.id, appt.patient_id)


def send_alarm_2(appt: Appointment, db: Session) -> None:
    """At-reminder-time alarm. Sets alarm_2_sent=True on success."""
    name = appt.patient.name
    _deliver(appt, _compose_alarm2_full(name, appt), _compose_alarm2_sms(name, appt), db)
    appt.alarm_2_sent = True
    db.commit()
    logger.info("Alarm 2 sent | appt=%s | patient=%s", appt.id, appt.patient_id)
