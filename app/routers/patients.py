# app/routers/patients.py
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datetime import date

from app.core.database import get_db
from app.core.risk_config import RUBRIC_VERSION
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.schemas.common import RiskOverrideRequest
from app.schemas.patient import PatientListItem, PatientResponse, PatientUpdate
from app.schemas.pregnancy import PregnancyResponse
from app.schemas.risk_assessment import RiskAssessmentResponse
from app.utils.access import get_patient_scoped, require_hospital
from app.utils.auth import get_current_user

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[PatientListItem])
def list_patients(
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """
    Hospital only — returns active patients belonging to the calling hospital.
    Each row includes: name, phone, age, status, current gestational age in weeks.
    Patients cannot call this endpoint (require_hospital raises 403).
    """
    patients = (
        db.query(Patient)
        .filter(Patient.hospital_id == caller_id, Patient.is_active.is_(True))
        .all()
    )
    today = date.today()
    return [
        PatientListItem(
            id=p.id,
            name=p.name,
            phone=p.phone,
            age=p.age,
            status=p.status or "active",
            current_ga_weeks=(today - p.lmp).days // 7 if p.lmp else None,
        )
        for p in patients
    ]


# ── Single patient CRUD ───────────────────────────────────────────────────────

@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Return full patient profile.
    Patient self → own record only.
    Hospital → own-hospital patients only; out-of-scope returns 404.
    """
    return get_patient_scoped(patient_id, current_user, db)


@router.patch("/{patient_id}", response_model=PatientResponse)
def update_patient(
    patient_id: UUID,
    body: PatientUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Update patient profile fields.
    Patient self → own record only.
    Hospital → own-hospital patients only; out-of-scope returns 404.
    """
    patient = get_patient_scoped(patient_id, current_user, db)

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(patient, field, value)

    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=204)
def delete_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Soft-delete a patient: sets is_active=False.
    The patient disappears from GET /patients and GET /patients/{id}, but the
    row and all linked history (messages, appointments, risk assessments) remain
    for audit purposes.
    Patient self → self-deactivation ("leave the system").
    Hospital → own patients only; out-of-scope returns 404.
    """
    patient = get_patient_scoped(patient_id, current_user, db)
    patient.is_active = False
    db.commit()


# ── Sub-resources ─────────────────────────────────────────────────────────────

@router.get("/{patient_id}/pregnancy", response_model=PregnancyResponse)
def get_pregnancy(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return the patient's current pregnancy record."""
    get_patient_scoped(patient_id, current_user, db)  # access-check only

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
    caller_id: str = Depends(require_hospital),
):
    """
    Clinician manually overrides a patient's risk level.
    Restricted to hospital users who own the patient; every change is logged.
    """
    current_user = {"type": "hospital", "user_id": caller_id}
    patient = get_patient_scoped(patient_id, current_user, db)

    if body.new_level not in ("low", "medium", "high"):
        raise HTTPException(status_code=400, detail="Level must be low, medium, or high")

    patient.risk_level = body.new_level
    patient.risk_level_set_at = datetime.now(timezone.utc)
    patient.risk_level_set_by = caller_id

    risk_record = RiskAssessment(
        patient_id=patient.id,
        computed_by=caller_id,
        inputs={"reason": body.reason, "override": True},
        rubric_version=RUBRIC_VERSION,
        result_level=body.new_level,
        score=None,
    )
    db.add(risk_record)
    db.commit()
    db.refresh(patient)

    try:
        from app.services.checkin_sender import send_checkin
        send_checkin(patient, db)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Post-override check-in attempt failed | patient=%s | %s", patient.id, exc
        )

    return patient


@router.get("/{patient_id}/risk-assessments", response_model=list[RiskAssessmentResponse])
def get_risk_assessments(
    patient_id: UUID,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """Return full risk level audit trail for a patient. Clinicians only."""
    current_user = {"type": "hospital", "user_id": caller_id}
    get_patient_scoped(patient_id, current_user, db)  # access-check + is_active

    return (
        db.query(RiskAssessment)
        .filter(RiskAssessment.patient_id == patient_id)
        .order_by(RiskAssessment.computed_at.desc())
        .all()
    )
