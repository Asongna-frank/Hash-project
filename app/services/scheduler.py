# app/services/scheduler.py
"""
APScheduler background jobs.

Local dev: runs inside the FastAPI process.
Production (AWS): swap the trigger for EventBridge + Lambda — the job functions
stay identical.

Job: check_appointment_reminders — every 15 minutes, find appointments due a
24h or 2h reminder and deliver them. The +/-15 min window absorbs the polling
interval so nothing is missed.
"""

import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, joinedload

from app.core.database import SessionLocal
from app.models.appointment import Appointment
from app.services.reminder_sender import send_24h_reminder, send_2h_reminder

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


def check_appointment_reminders() -> None:
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # --- 24h window ---
        due_24h = db.query(Appointment).options(
            joinedload(Appointment.patient)
        ).filter(
            Appointment.is_deleted == False,
            Appointment.reminder_24h_sent == False,
            Appointment.appointment_datetime >= now + timedelta(hours=23),
            Appointment.appointment_datetime <= now + timedelta(hours=25),
        ).all()

        for appt in due_24h:
            try:
                send_24h_reminder(appt, db)
            except Exception as exc:
                db.rollback()
                logger.error("24h reminder failed | appt=%s | %s", appt.id, exc)

        # --- 2h window ---
        due_2h = db.query(Appointment).options(
            joinedload(Appointment.patient)
        ).filter(
            Appointment.is_deleted == False,
            Appointment.reminder_2h_sent == False,
            Appointment.appointment_datetime >= now + timedelta(hours=1, minutes=45),
            Appointment.appointment_datetime <= now + timedelta(hours=2, minutes=15),
        ).all()

        for appt in due_2h:
            try:
                send_2h_reminder(appt, db)
            except Exception as exc:
                db.rollback()
                logger.error("2h reminder failed | appt=%s | %s", appt.id, exc)

        if due_24h or due_2h:
            logger.info("Reminder check | 24h=%d | 2h=%d", len(due_24h), len(due_2h))
    except Exception as exc:
        logger.error("Reminder scheduler job failed: %s", exc)
    finally:
        db.close()


scheduler.add_job(
    check_appointment_reminders,
    trigger="interval",
    minutes=15,
    id="appointment_reminders",
    replace_existing=True,
)


def send_all_daily_tips() -> None:
    """
    Daily job — fires at 07:00 UTC (08:00 Cameroon / Africa/Douala).
    Iterates all active and post-loss patients and delivers each their
    personalized daily tip. Each patient is isolated in try/except so one
    failure never blocks the rest of the batch.
    """
    from app.models.patient import Patient
    from app.services.tip_sender import send_daily_tip

    db: Session = SessionLocal()
    processed = failed = 0
    try:
        patients = (
            db.query(Patient)
            .filter(Patient.status.in_(["active", "post_loss"]))
            .all()
        )

        for patient in patients:
            try:
                send_daily_tip(patient, db)
                processed += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                logger.error("Daily tip failed | patient=%s | %s", patient.id, exc)

        logger.info(
            "Daily tips complete | processed=%d failed=%d total=%d",
            processed, failed, len(patients),
        )
    except Exception as exc:
        logger.error("Daily tip scheduler job failed: %s", exc)
    finally:
        db.close()


scheduler.add_job(
    send_all_daily_tips,
    trigger=CronTrigger(hour=7, minute=0, timezone="UTC"),
    id="daily_tips",
    replace_existing=True,
)


def send_all_checkins() -> None:
    """
    Daily job — fires at 08:00 UTC (09:00 Cameroon / Africa/Douala).
    Iterates all active and post-loss patients and delivers a proactive
    wellness check-in to those whose risk-level interval has elapsed.

    Delivery cadence per risk level:
      high   → daily   (≥20 h since last check-in)
      medium → weekly  (≥6.5 days since last check-in)
      low    → every fortnight (≥13 days), PLUS milestone weeks 12/20/28/36

    Each patient is isolated in try/except so one failure never blocks the rest.
    """
    from app.models.patient import Patient
    from app.services.checkin_sender import send_checkin

    db: Session = SessionLocal()
    sent = failed = 0
    try:
        patients = (
            db.query(Patient)
            .filter(Patient.status.in_(["active", "post_loss"]))
            .all()
        )
        for patient in patients:
            try:
                if send_checkin(patient, db):
                    sent += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                logger.error("Check-in failed | patient=%s | %s", patient.id, exc)

        logger.info(
            "Check-ins complete | sent=%d failed=%d total=%d",
            sent, failed, len(patients),
        )
    except Exception as exc:
        logger.error("Check-in scheduler job failed: %s", exc)
    finally:
        db.close()


scheduler.add_job(
    send_all_checkins,
    trigger=CronTrigger(hour=8, minute=0, timezone="UTC"),
    id="daily_checkins",
    replace_existing=True,
)
