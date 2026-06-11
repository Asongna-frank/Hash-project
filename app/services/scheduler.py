# app/services/scheduler.py
"""
APScheduler background jobs.

Local dev: runs inside the FastAPI process (started in startup_event in main.py).
Production (AWS): replace with EventBridge + Lambda — the job functions stay identical.

Guard against double-start:
  Under `uvicorn --reload`, the file watcher can trigger startup_event twice in
  the same process.  We check `scheduler.running` before calling start().
  For multi-worker deployments (uvicorn --workers N) use a dedicated scheduler
  process or a distributed task queue — running N scheduler copies sends N copies
  of every alarm.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, joinedload

from app.core.database import SessionLocal
from app.models.appointment import Appointment
from app.services.reminder_sender import send_alarm_1, send_alarm_2

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")

# ±half-interval window so no alarm is missed between polls (5 min interval → 3 min).
_WINDOW = timedelta(minutes=3)
_ALARM1_OFFSET = timedelta(minutes=30)  # alarm 1 fires 30 min before reminder_datetime


def check_appointment_reminders() -> None:
    """
    Every 5 minutes: find appointments whose alarm windows are open and deliver.

    Alarm 1 window: now ∈ [reminder_dt − 30min − 3min,  reminder_dt − 30min + 3min]
    Alarm 2 window: now ∈ [reminder_dt − 3min,            reminder_dt + 3min]
    """
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Alarm 1: 30 min before reminder_datetime
        a1_low  = now + _ALARM1_OFFSET - _WINDOW   # reminder_dt >= now+27min
        a1_high = now + _ALARM1_OFFSET + _WINDOW   # reminder_dt <= now+33min

        due_alarm1 = (
            db.query(Appointment)
            .options(joinedload(Appointment.patient))
            .filter(
                Appointment.is_deleted.is_(False),
                Appointment.alarm_1_sent.is_(False),
                Appointment.reminder_datetime >= a1_low,
                Appointment.reminder_datetime <= a1_high,
            )
            .all()
        )

        for appt in due_alarm1:
            try:
                send_alarm_1(appt, db)
            except Exception as exc:
                db.rollback()
                logger.error("Alarm 1 failed | appt=%s | %s", appt.id, exc)

        # Alarm 2: at reminder_datetime
        a2_low  = now - _WINDOW   # reminder_dt >= now−3min
        a2_high = now + _WINDOW   # reminder_dt <= now+3min

        due_alarm2 = (
            db.query(Appointment)
            .options(joinedload(Appointment.patient))
            .filter(
                Appointment.is_deleted.is_(False),
                Appointment.alarm_2_sent.is_(False),
                Appointment.reminder_datetime >= a2_low,
                Appointment.reminder_datetime <= a2_high,
            )
            .all()
        )

        for appt in due_alarm2:
            try:
                send_alarm_2(appt, db)
            except Exception as exc:
                db.rollback()
                logger.error("Alarm 2 failed | appt=%s | %s", appt.id, exc)

        if due_alarm1 or due_alarm2:
            logger.info(
                "Alarm check | alarm_1=%d alarm_2=%d",
                len(due_alarm1), len(due_alarm2),
            )
    except Exception as exc:
        logger.error("Reminder scheduler job failed: %s", exc)
    finally:
        db.close()


scheduler.add_job(
    check_appointment_reminders,
    trigger="interval",
    minutes=5,
    id="appointment_reminders",
    replace_existing=True,
)


def send_all_daily_tips() -> None:
    """Daily job — 07:00 UTC (08:00 Cameroon). Sends personalised daily tip to every active patient."""
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
    trigger=CronTrigger(hour=10, minute=30, timezone="UTC"),
    id="daily_tips",
    replace_existing=True,
)


def send_all_checkins() -> None:
    """Daily job — 08:00 UTC. Sends proactive check-ins to patients whose risk-interval has elapsed."""
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
