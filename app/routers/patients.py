# app/routers/patients.py
from datetime import date, datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.risk_config import RUBRIC_VERSION
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.schemas.patient import PatientResponse
from app.schemas.pregnancy import PregnancyResponse
from app.schemas.risk_assessment import RiskAssessmentResponse
from app.schemas.common import RiskOverrideRequest
from app.utils.auth import get_current_user

router = APIRouter()


def _get_patient_or_404(patient_id: UUID, db: Session) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Return full patient profile.
    Accessible by the patient themselves or any hospital user.
    """
    patient = _get_patient_or_404(patient_id, db)

    # Access control: patient can only read their own record
    if current_user["type"] == "patient":
        if str(patient.id) != current_user["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")

    return patient


@router.get("/{patient_id}/pregnancy", response_model=PregnancyResponse)
def get_pregnancy(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return the patient's current pregnancy record."""
    _get_patient_or_404(patient_id, db)

    pregnancy = (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient_id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )
    if not pregnancy:
        raise HTTPException(status_code=404, detail="No pregnancy record found")
    return pregnancy


@router.patch("/{patient_id}/risk-level", response_model=PatientResponse)
def override_risk_level(
    patient_id: UUID,
    body: RiskOverrideRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Clinician manually overrides a patient's risk level.
    Restricted to hospital users only.
    Every change is logged to risk_assessments.
    """
    # Restrict to clinicians only
    if current_user["type"] != "hospital":
        raise HTTPException(status_code=403, detail="Clinicians only")

    if body.new_level not in ("low", "medium", "high"):
        raise HTTPException(status_code=400, detail="Level must be low, medium, or high")

    patient = _get_patient_or_404(patient_id, db)

    # Update patient risk level
    patient.risk_level = body.new_level
    patient.risk_level_set_at = datetime.now(timezone.utc)
    patient.risk_level_set_by = current_user["user_id"]

    # Audit record
    risk_record = RiskAssessment(
        patient_id=patient.id,
        computed_by=current_user["user_id"],
        inputs={"reason": body.reason, "override": True},
        rubric_version=RUBRIC_VERSION,
        result_level=body.new_level,
        score=None,  # no numeric score for manual overrides
    )
    db.add(risk_record)
    db.commit()
    db.refresh(patient)

    # TODO M3: signal check-in scheduler to update cadence for this patient

    return patient


@router.get("/{patient_id}/risk-assessments", response_model=list[RiskAssessmentResponse])
def get_risk_assessments(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Return full risk level audit trail for a patient.
    Clinicians only.
    """
    if current_user["type"] != "hospital":
        raise HTTPException(status_code=403, detail="Clinicians only")

    _get_patient_or_404(patient_id, db)

    records = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.patient_id == patient_id)
        .order_by(RiskAssessment.computed_at.desc())
        .all()
    )
    return records