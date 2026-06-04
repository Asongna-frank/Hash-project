# app/routers/hospital_patients.py
"""
Hospital-side patient creation (choronko / SMS patients).

Mounted at /hospital/patients. Choronko patients have no app and cannot
self-signup, so the hospital onboards them here. Identity is phone-only:
no password, and (deliberately) no individual personnel attribution — personnel
have no logins, so any "created_by" would be client-asserted and unverifiable.
The hospital_id always comes from the auth token, never the request body.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.patient import Patient
from app.schemas.patient import HospitalPatientCreate, PatientResponse
from app.services.audit import write_audit
from app.services.patient_onboarding import onboard_patient
from app.utils.access import require_hospital
from app.utils.phone import normalize_phone_or_422

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=PatientResponse,
    status_code=201,
    summary="Create a choronko patient (hospital)",
    description=(
        "Hospital-only. Onboards an SMS (choronko) patient: account_type=choronko, "
        "no password, hospital_id taken from the token (a body hospital_id is "
        "ignored — a hospital cannot create patients elsewhere). Phone is "
        "normalized to E.164 and must be unique. Runs baseline risk scoring and "
        "creates the pregnancy record, same as self-signup. No personnel "
        "attribution is stored. Patients calling this get 403."
    ),
)
def hospital_create_patient(
    body: HospitalPatientCreate,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    # Normalize + enforce unique phone
    patient_phone = normalize_phone_or_422(body.phone, "phone")
    if db.query(Patient).filter(Patient.phone == patient_phone).first():
        raise HTTPException(status_code=400, detail="Phone number already registered")

    # Onboard via the shared service — choronko, no password, hospital from TOKEN.
    data = {**body.model_dump(), "phone": patient_phone}
    patient = onboard_patient(
        db, data,
        hospital_id=caller_hospital_id,
        account_type="choronko",
        hashed_password=None,
    )

    # Audit: hospital created the patient. No personnel attribution (by design).
    write_audit(
        db, actor_type="hospital", actor_id=caller_hospital_id,
        action="patient.create", target_type="patient", target_id=patient.id,
        details={"account_type": "choronko"},
    )
    db.commit()
    db.refresh(patient)
    logger.info("Choronko patient created | hospital=%s | patient=%s",
                caller_hospital_id, patient.id)
    return patient
