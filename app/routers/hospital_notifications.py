# app/routers/hospital_notifications.py
"""
Hospital-side notification bell.

Two read endpoints powering the dashboard's bell badge and dropdown feed:
- /summary -> counts only (badge)
- ""       -> merged event feed (dropdown list)

State rules (deliberately simple):
- Alerts are the only items needing explicit acknowledgement — done via the
  existing PATCH /alerts/{id}; once acked they leave the "new" counts.
- Missed-check-in flags clear automatically when the patient replies.
- Appointments and new registrations are informational (no ack needed).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.alert import Alert
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.utils.access import require_hospital

router = APIRouter()
logger = logging.getLogger(__name__)


class NotificationSummary(BaseModel):
    new_alerts: int = Field(..., examples=[3])
    critical_alerts: int = Field(..., examples=[1])  # subset of new_alerts
    missed_checkin_patients: int = Field(..., examples=[2])
    appointments_today: int = Field(..., examples=[4])
    new_patients_24h: int = Field(..., examples=[1])
    badge_count: int = Field(..., examples=[5],
                             description="new_alerts + missed_checkin_patients — the red bell number")


def _today_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


@router.get(
    "/summary",
    response_model=NotificationSummary,
    summary="Notification bell counts (hospital)",
    description=(
        "Hospital-only. Lightweight counts for the dashboard bell badge: "
        "unacknowledged alerts (with the critical subset), patients currently "
        "flagged for missed check-ins, today's appointments, and patients "
        "registered in the last 24h. badge_count = new_alerts + "
        "missed_checkin_patients. Poll this, or simply recompute on every "
        "alerts-WebSocket frame."
    ),
)
def notification_summary(
    db: Session = Depends(get_db),
    hospital_id: str = Depends(require_hospital),
):
    new_alerts = (
        db.query(Alert)
        .filter(Alert.hospital_id == hospital_id, Alert.status == "new")
        .all()
    )
    critical = sum(1 for a in new_alerts
                   if a.triage_level == "high" or a.source == "emergency_button")

    missed = (
        db.query(Patient)
        .filter(Patient.hospital_id == hospital_id,
                Patient.is_active.is_(True),
                Patient.missed_checkin_flag.is_(True))
        .count()
    )

    day_start, day_end = _today_bounds()
    appts_today = (
        db.query(Appointment)
        .filter(Appointment.hospital_id == hospital_id,
                Appointment.is_deleted.is_(False),
                Appointment.appointment_datetime >= day_start,
                Appointment.appointment_datetime < day_end)
        .count()
    )

    new_patients = (
        db.query(Patient)
        .filter(Patient.hospital_id == hospital_id,
                Patient.is_active.is_(True),
                Patient.created_at >= datetime.now(timezone.utc) - timedelta(hours=24))
        .count()
    )

    return NotificationSummary(
        new_alerts=len(new_alerts),
        critical_alerts=critical,
        missed_checkin_patients=missed,
        appointments_today=appts_today,
        new_patients_24h=new_patients,
        badge_count=len(new_alerts) + missed,
    )


class NotificationItem(BaseModel):
    type: str = Field(..., examples=["alert"])  # alert|missed_checkin|appointment_today|new_patient
    title: str = Field(..., examples=["🚨 Maria Nkeng — emergency button"])
    body: Optional[str] = Field(default=None, examples=["EMERGENCY BUTTON pressed"])
    patient_id: Optional[str] = Field(default=None)
    ref_id: Optional[str] = Field(default=None,
                                  description="alert id (for PATCH /alerts/{id}) or appointment id")
    priority: str = Field(default="normal", examples=["critical"])  # critical|normal|info
    created_at: Optional[datetime] = Field(default=None)


@router.get(
    "",
    response_model=list[NotificationItem],
    summary="Notification feed (hospital bell dropdown)",
    description=(
        "Hospital-only. Merged recent events, newest first (max 50): "
        "unacknowledged alerts (priority critical/normal — ack them via "
        "PATCH /alerts/{ref_id}), patients flagged for missed check-ins, "
        "today's appointments, and patients registered in the last 24h "
        "(both informational). Each item carries patient_id so a tap can "
        "deep-link to the patient page."
    ),
)
def notification_feed(
    db: Session = Depends(get_db),
    hospital_id: str = Depends(require_hospital),
):
    items: list[NotificationItem] = []

    # 1) Unacked alerts
    alerts = (
        db.query(Alert, Patient)
        .join(Patient, Patient.id == Alert.patient_id)
        .filter(Alert.hospital_id == hospital_id, Alert.status == "new")
        .order_by(Alert.created_at.desc())
        .limit(25)
        .all()
    )
    for a, p in alerts:
        critical = a.triage_level == "high" or a.source == "emergency_button"
        items.append(NotificationItem(
            type="alert",
            title=f"{'🚨' if critical else '⚠️'} {p.name} — {a.source.replace('_', ' ')}",
            body=a.reason,
            patient_id=str(p.id),
            ref_id=str(a.id),
            priority="critical" if critical else "normal",
            created_at=a.created_at,
        ))

    # 2) Missed-check-in flags
    flagged = (
        db.query(Patient)
        .filter(Patient.hospital_id == hospital_id,
                Patient.is_active.is_(True),
                Patient.missed_checkin_flag.is_(True))
        .all()
    )
    for p in flagged:
        items.append(NotificationItem(
            type="missed_checkin",
            title=f"📵 {p.name} — {p.consecutive_missed_checkins} missed check-ins",
            body="Consider reaching out to her.",
            patient_id=str(p.id),
            priority="normal",
            created_at=None,
        ))

    # 3) Today's appointments
    day_start, day_end = _today_bounds()
    appts = (
        db.query(Appointment, Patient)
        .join(Patient, Patient.id == Appointment.patient_id)
        .filter(Appointment.hospital_id == hospital_id,
                Appointment.is_deleted.is_(False),
                Appointment.appointment_datetime >= day_start,
                Appointment.appointment_datetime < day_end)
        .order_by(Appointment.appointment_datetime.asc())
        .all()
    )
    for a, p in appts:
        items.append(NotificationItem(
            type="appointment_today",
            title=f"📅 {p.name} — {a.title}",
            body=a.appointment_datetime.strftime("Today %H:%M"),
            patient_id=str(p.id),
            ref_id=str(a.id),
            priority="info",
            created_at=a.appointment_datetime,
        ))

    # 4) New registrations (last 24h)
    new_patients = (
        db.query(Patient)
        .filter(Patient.hospital_id == hospital_id,
                Patient.is_active.is_(True),
                Patient.created_at >= datetime.now(timezone.utc) - timedelta(hours=24))
        .order_by(Patient.created_at.desc())
        .all()
    )
    for p in new_patients:
        items.append(NotificationItem(
            type="new_patient",
            title=f"🌱 New patient: {p.name}",
            body=f"{p.account_type} · risk {p.risk_level or 'n/a'}",
            patient_id=str(p.id),
            priority="info",
            created_at=p.created_at,
        ))

    # newest first; undated (flag) items float to the top just after alerts
    items.sort(key=lambda i: (i.created_at is None, i.created_at or datetime.min.replace(tzinfo=timezone.utc)),
               reverse=True)
    return items[:50]
