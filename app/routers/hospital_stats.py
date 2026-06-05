# app/routers/hospital_stats.py
"""
Dashboard overview stats (M8 home header + SRS analyst metrics: enrolment,
alert volumes, response times).
"""

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.alert import Alert
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.utils.access import require_hospital

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "",
    summary="Dashboard overview stats",
    description=(
        "Hospital-only. One call for the dashboard home header: patient counts "
        "by risk/track/status, missed-check-in flags, alert volumes for the "
        "last 7 days with average acknowledgement time (minutes), a 30-day "
        "enrolment series for the mini chart, and the next 7 days' "
        "appointment count."
    ),
)
def hospital_stats(
    db: Session = Depends(get_db),
    hospital_id: str = Depends(require_hospital),
):
    now = datetime.now(timezone.utc)

    # ── patients ──────────────────────────────────────────────────────────────
    patients = (
        db.query(Patient)
        .filter(Patient.hospital_id == hospital_id, Patient.is_active.is_(True))
        .all()
    )
    by_risk = {"high": 0, "medium": 0, "low": 0, "unassessed": 0}
    by_track = {"smartphone": 0, "choronko": 0}
    by_status = {"active": 0, "post_loss": 0, "delivered": 0}
    missed_flags = 0
    for p in patients:
        by_risk[p.risk_level if p.risk_level in by_risk else "unassessed"] += 1
        by_track[p.account_type if p.account_type in by_track else "smartphone"] += 1
        by_status[p.status if p.status in by_status else "active"] += 1
        if p.missed_checkin_flag:
            missed_flags += 1

    # ── alerts, last 7 days ───────────────────────────────────────────────────
    week_ago = now - timedelta(days=7)
    alerts = (
        db.query(Alert)
        .filter(Alert.hospital_id == hospital_id, Alert.created_at >= week_ago)
        .all()
    )
    critical = sum(1 for a in alerts
                   if a.triage_level == "high" or a.source == "emergency_button")
    ack_times = [
        (a.acknowledged_at - a.created_at).total_seconds() / 60
        for a in alerts
        if a.acknowledged_at is not None and a.created_at is not None
    ]
    by_source: dict[str, int] = {}
    for a in alerts:
        by_source[a.source] = by_source.get(a.source, 0) + 1

    # ── enrolment, last 30 days (fill gaps with 0 for an even chart) ─────────
    start_day = (now - timedelta(days=29)).date()
    rows = (
        db.query(sa_func.date(Patient.created_at), sa_func.count())
        .filter(Patient.hospital_id == hospital_id,
                Patient.created_at >= start_day)
        .group_by(sa_func.date(Patient.created_at))
        .all()
    )
    counts = {d: c for d, c in rows}
    enrolment = []
    day = start_day
    while day <= now.date():
        enrolment.append({"date": day.isoformat(), "count": int(counts.get(day, 0))})
        day += timedelta(days=1)

    # ── appointments, next 7 days ─────────────────────────────────────────────
    appts_next_7d = (
        db.query(Appointment)
        .filter(Appointment.hospital_id == hospital_id,
                Appointment.is_deleted.is_(False),
                Appointment.appointment_datetime >= now,
                Appointment.appointment_datetime < now + timedelta(days=7))
        .count()
    )

    return {
        "patients": {
            "total": len(patients),
            "by_risk": by_risk,
            "by_track": by_track,
            "by_status": by_status,
            "missed_checkin_flags": missed_flags,
        },
        "alerts_7d": {
            "total": len(alerts),
            "critical": critical,
            "unacknowledged": sum(1 for a in alerts if a.status == "new"),
            "avg_ack_minutes": round(sum(ack_times) / len(ack_times), 1) if ack_times else None,
            "by_source": by_source,
        },
        "enrolment_30d": enrolment,
        "appointments_next_7d": appts_next_7d,
    }
