# app/routers/hospital_appointments.py
"""
Hospital-facing appointment CRUD.

Mounted at /hospital/appointments in main.py.
All endpoints require hospital auth and are scoped to the caller's own hospital.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.schemas.appointment import (
    AppointmentResponse,
    AppointmentUpdate,
    HospitalAppointmentCreate,
    HospitalAppointmentResponse,
)
from app.services.sms_service import sms_service
from app.utils.access import require_hospital
from app.utils.phone import normalize_phone_or_422

router = APIRouter()
logger = logging.getLogger(__name__)

# Reminder offset: hospital-created appointments get reminder_datetime = appt − this
_HOSPITAL_REMINDER_OFFSET = timedelta(minutes=30)


def _compose_confirmation(patient_name: str, appt: Appointment) -> str:
    dt = appt.appointment_datetime
    return (
        f"HASH: Hi {patient_name}, your appointment '{appt.title}' has been "
        f"scheduled for {dt.strftime('%A %d %b %Y')} at {dt.strftime('%I:%M %p')}. "
        f"You will receive a reminder. Reply STOP to opt out."
    )


# ── CREATE ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=HospitalAppointmentResponse,
    status_code=201,
    summary="Create an appointment for a patient (hospital)",
    description=(
        "Hospital-only. Creates an appointment on behalf of one of the calling "
        "hospital's patients, looked up by phone (normalized to E.164; invalid → "
        "422, not-your-patient → 404). reminder_datetime is auto-set to 30 min "
        "before the appointment. Sends an immediate confirmation SMS — the "
        "response reports the SMS outcome, and SMS failure does not fail the call."
    ),
)
def hospital_create_appointment(
    body: HospitalAppointmentCreate,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """
    Hospital creates an appointment on behalf of a (choronko) patient.

    Flow:
    1. Look up patient by phone AND hospital_id == caller (404 if not found or
       out-of-scope — don't leak that the phone exists at another hospital).
    2. Compute reminder_datetime = appointment_datetime − 30 min.
    3. Persist appointment (created_by="hospital").
    4. Send an immediate confirmation SMS. Failure is logged but does NOT
       fail the request — confirmation_sent reflects the SMS outcome.
    """
    # 1. Resolve patient — normalize phone to E.164 first, scoped to THIS hospital
    patient_phone = normalize_phone_or_422(body.patient_phone, "patient phone")
    patient = db.query(Patient).filter(
        Patient.phone == patient_phone,
        Patient.hospital_id == caller_id,
        Patient.is_active.is_(True),
    ).first()
    if not patient:
        raise HTTPException(
            status_code=404,
            detail="Patient not found",  # intentionally vague — don't leak cross-hospital phones
        )

    # 2. Compute reminder_datetime
    reminder_dt = body.appointment_datetime - _HOSPITAL_REMINDER_OFFSET

    # 3. Create appointment
    appointment = Appointment(
        patient_id=patient.id,
        hospital_id=patient.hospital_id,
        title=body.title,
        notes=body.notes,
        appointment_datetime=body.appointment_datetime,
        reminder_datetime=reminder_dt,
        created_by="hospital",
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    # 4. Immediate confirmation SMS (non-fatal on failure)
    sms_ok = False
    sms_error = None
    try:
        text = _compose_confirmation(patient.name, appointment)
        result = sms_service.send_sms(to=patient.phone, message=text)
        if result.ok:
            sms_ok = True
            appointment.confirmation_sent = True
            db.commit()
            logger.info(
                "Confirmation SMS sent | appt=%s | patient=%s",
                appointment.id, patient.id,
            )
        else:
            sms_error = result.error
            logger.warning(
                "Confirmation SMS failed (appointment kept) | appt=%s | error=%s",
                appointment.id, result.error,
            )
    except Exception as exc:
        sms_error = str(exc)
        logger.error(
            "Confirmation SMS exception (appointment kept) | appt=%s | %s",
            appointment.id, exc,
        )

    return HospitalAppointmentResponse(
        id=appointment.id,
        patient_id=appointment.patient_id,
        hospital_id=appointment.hospital_id,
        title=appointment.title,
        notes=appointment.notes,
        appointment_datetime=appointment.appointment_datetime,
        reminder_datetime=appointment.reminder_datetime,
        created_by=appointment.created_by,
        alarm_1_sent=appointment.alarm_1_sent,
        alarm_2_sent=appointment.alarm_2_sent,
        confirmation_sent=appointment.confirmation_sent,
        is_deleted=appointment.is_deleted,
        created_at=appointment.created_at,
        sms_confirmation_ok=sms_ok,
        sms_confirmation_error=sms_error,
    )


# ── LIST ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[AppointmentResponse],
    summary="List the hospital's appointments",
    description=(
        "Hospital-only. Returns all active (non-deleted) appointments for the "
        "calling hospital's patients, soonest first. Pass ?upcoming_only=true to "
        "exclude past appointments."
    ),
)
def hospital_list_appointments(
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
    upcoming_only: bool = Query(default=False),
):
    """Return all active (not deleted) appointments for the calling hospital's patients."""
    now = datetime.now(timezone.utc)
    query = db.query(Appointment).filter(
        Appointment.hospital_id == caller_id,
        Appointment.is_deleted.is_(False),
    )
    if upcoming_only:
        query = query.filter(Appointment.appointment_datetime > now)
    return query.order_by(Appointment.appointment_datetime.asc()).all()


# ── PATCH ─────────────────────────────────────────────────────────────────────

@router.patch(
    "/{appointment_id}",
    response_model=AppointmentResponse,
    summary="Edit an appointment (hospital)",
    description=(
        "Hospital-only. Edits title, notes, or datetimes of an appointment the "
        "calling hospital owns (else 404). Future-datetime validation applies."
    ),
)
def hospital_update_appointment(
    appointment_id: UUID,
    body: AppointmentUpdate,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """Edit an appointment. Caller must own the appointment (same hospital)."""
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.is_deleted.is_(False),
    ).first()
    if not appointment or str(appointment.hospital_id) != caller_id:
        raise HTTPException(status_code=404, detail="Appointment not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(appointment, field, value)

    db.commit()
    db.refresh(appointment)
    return appointment


# ── DELETE ────────────────────────────────────────────────────────────────────

@router.delete(
    "/{appointment_id}",
    status_code=204,
    summary="Soft-delete an appointment (hospital)",
    description=(
        "Hospital-only. Soft-deletes an appointment the calling hospital owns "
        "(is_deleted=True; never hard-deleted). Unknown/not-owned → 404."
    ),
)
def hospital_delete_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """Soft-delete an appointment. Caller must own it."""
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.is_deleted.is_(False),
    ).first()
    if not appointment or str(appointment.hospital_id) != caller_id:
        raise HTTPException(status_code=404, detail="Appointment not found")

    appointment.is_deleted = True
    db.commit()
    logger.info("Hospital soft-deleted appointment | id=%s", appointment_id)
