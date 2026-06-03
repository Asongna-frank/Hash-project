# app/routers/appointments.py
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentDeleteRequest,
)
from app.utils.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_patient(patient_id: str, db: Session) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


# CREATE — POST /appointments
@router.post("", response_model=AppointmentResponse, status_code=201)
def create_appointment(
    body: AppointmentCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["type"] != "patient":
        raise HTTPException(status_code=403, detail="Patients only")

    patient = _get_patient(current_user["user_id"], db)

    if body.appointment_datetime <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Appointment datetime must be in the future")

    appointment = Appointment(
        patient_id=patient.id,
        hospital_id=patient.hospital_id,   # taken from patient, never submitted
        title=body.title,
        notes=body.notes,
        appointment_datetime=body.appointment_datetime,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    logger.info("Appointment created | patient=%s | dt=%s", patient.id, body.appointment_datetime)
    return appointment


# LIST — GET /appointments
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
            Appointment.is_deleted == False,
        )
    elif current_user["type"] == "hospital":
        query = db.query(Appointment).filter(
            Appointment.hospital_id == current_user["user_id"],
            Appointment.is_deleted == False,
        )
    else:
        raise HTTPException(status_code=403, detail="Access denied")

    if upcoming_only:
        query = query.filter(Appointment.appointment_datetime > now)

    return query.order_by(Appointment.appointment_datetime.asc()).all()


# DELETE ONE — DELETE /appointments/{id}
@router.delete("/{appointment_id}", status_code=200)
def delete_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.is_deleted == False,
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


# DELETE MANY — DELETE /appointments
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
            Appointment.is_deleted == False,
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
    logger.info("Bulk delete | deleted=%s | not_found=%s | denied=%s", deleted, not_found, denied)
    return {"deleted": deleted, "not_found": not_found, "access_denied": denied}
