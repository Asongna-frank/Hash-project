# app/routers/appointments.py
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentDeleteRequest,
    AppointmentResponse,
)
from app.utils.access import require_patient
from app.utils.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Patient-created appointments ──────────────────────────────────────────────

@router.post("", response_model=AppointmentResponse, status_code=201)
def create_appointment(
    body: AppointmentCreate,
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    """
    Patient books their own appointment.
    hospital_id is taken from the patient's record — never submitted.
    reminder_datetime is patient-supplied (validated: future and <= appointment).
    No confirmation SMS is sent for patient-created appointments.
    """
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    appointment = Appointment(
        patient_id=patient.id,
        hospital_id=patient.hospital_id,
        title=body.title,
        notes=body.notes,
        appointment_datetime=body.appointment_datetime,
        reminder_datetime=body.reminder_datetime,
        created_by="patient",
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    logger.info(
        "Appointment created by patient | patient=%s | appt=%s | reminder=%s",
        patient.id, body.appointment_datetime, body.reminder_datetime,
    )
    return appointment


@router.get("", response_model=list[AppointmentResponse])
def list_appointments(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    upcoming_only: bool = Query(default=False),
):
    now = datetime.now(timezone.utc)

    if current_user["type"] == "patient":
        query = db.query(Appointment).filter(
            Appointment.patient_id == current_user["user_id"],
            Appointment.is_deleted.is_(False),
        )
    elif current_user["type"] == "hospital":
        query = db.query(Appointment).filter(
            Appointment.hospital_id == current_user["user_id"],
            Appointment.is_deleted.is_(False),
        )
    else:
        raise HTTPException(status_code=403, detail="Access denied")

    if upcoming_only:
        query = query.filter(Appointment.appointment_datetime > now)

    return query.order_by(Appointment.appointment_datetime.asc()).all()


@router.delete("/{appointment_id}", status_code=200)
def delete_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.is_deleted.is_(False),
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if current_user["type"] == "patient":
        if str(appointment.patient_id) != current_user["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")
    elif current_user["type"] == "hospital":
        if str(appointment.hospital_id) != current_user["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")

    appointment.is_deleted = True
    db.commit()
    logger.info("Appointment soft-deleted | id=%s", appointment_id)
    return {"deleted": True, "id": str(appointment_id)}


@router.delete("", status_code=200)
def delete_appointments_bulk(
    body: AppointmentDeleteRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    deleted, not_found, denied = [], [], []

    for appt_id in body.ids:
        appointment = db.query(Appointment).filter(
            Appointment.id == appt_id,
            Appointment.is_deleted.is_(False),
        ).first()
        if not appointment:
            not_found.append(str(appt_id))
            continue

        if current_user["type"] == "patient":
            if str(appointment.patient_id) != current_user["user_id"]:
                denied.append(str(appt_id))
                continue
        elif current_user["type"] == "hospital":
            if str(appointment.hospital_id) != current_user["user_id"]:
                denied.append(str(appt_id))
                continue

        appointment.is_deleted = True
        deleted.append(str(appt_id))

    db.commit()
    return {"deleted": deleted, "not_found": not_found, "access_denied": denied}
