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
from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.schemas.patient import HospitalPatientCreate, PatientResponse
from app.services.audit import write_audit
from app.services.patient_onboarding import onboard_patient
from app.services.post_loss import activate_post_loss, mark_live_birth
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


# ── pregnancy outcome (clinician marks Loss / Live Birth) ─────────────────────

class OutcomeRequest(BaseModel):
    """Clinician records the pregnancy outcome. Marking 'loss' is the ONLY way
    the post-loss care track activates (SRS hard rule — never from chat)."""
    outcome: Literal["loss", "live_birth"] = Field(..., examples=["loss"])
    loss_date: Optional[date] = Field(
        default=None, examples=["2026-06-04"],
        description="Date of loss (loss only). Defaults to today.",
    )
    ga_at_loss: Optional[int] = Field(
        default=None, ge=0, le=44, examples=[18],
        description="Gestational age in weeks at loss (loss only). Computed from LMP if omitted.",
    )

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"outcome": "loss", "loss_date": "2026-06-04", "ga_at_loss": 18},
        {"outcome": "live_birth"},
    ]})


@router.post(
    "/{patient_id}/outcome",
    response_model=PatientResponse,
    summary="Mark pregnancy outcome (Loss / Live Birth)",
    description=(
        "Hospital-only, own patients only (others → 404). Marking 'loss' "
        "activates the post-loss care track (M9): routine tips/check-ins/EDD "
        "stop, risk escalates to high with an audit trail, and Dr Elvira's "
        "approved opener is sent on the patient's own channel (app push or "
        "SMS for choronko). This endpoint is the ONLY way the post-loss track "
        "activates — chat detection only flags for review. Marking 'live_birth' "
        "stops routine pregnancy messaging and sets status=delivered. "
        "A patient already in that state returns 409."
    ),
)
def mark_pregnancy_outcome(
    patient_id: UUID,
    body: OutcomeRequest,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    # Out-of-scope patients look non-existent (no cross-hospital id leaks).
    if not patient or str(patient.hospital_id) != caller_hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    pregnancy = (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient.id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )

    if body.outcome == "loss":
        if patient.status == "post_loss":
            raise HTTPException(status_code=409, detail="Patient is already on the post-loss track")
        activate_post_loss(
            db, patient, pregnancy,
            activated_by=caller_hospital_id,
            loss_date=body.loss_date,
            ga_at_loss=body.ga_at_loss,
        )
    else:  # live_birth
        if patient.status == "delivered":
            raise HTTPException(status_code=409, detail="Live birth already recorded")
        mark_live_birth(db, patient, pregnancy)

    write_audit(
        db, actor_type="hospital", actor_id=caller_hospital_id,
        action=f"pregnancy.outcome.{body.outcome}", target_type="patient",
        target_id=patient.id,
        details={"loss_date": str(body.loss_date) if body.loss_date else None,
                 "ga_at_loss": body.ga_at_loss},
    )
    db.commit()
    db.refresh(patient)
    logger.info("Pregnancy outcome marked | hospital=%s | patient=%s | outcome=%s",
                caller_hospital_id, patient.id, body.outcome)
    return patient
