"""
Reusable access-control guards.

All endpoints use one of these guards rather than scattered inline type-checks.

Design rules:
- `require_hospital` / `require_patient` are FastAPI Depends()-compatible callables.
  Declare them with Depends() in the endpoint signature to get automatic 403 enforcement.
- `get_patient_scoped` / `get_personnel_owned` take path params — call them inline
  inside the endpoint body after obtaining current_user and db.
- Out-of-scope patient/personnel requests return 404 (not 403) so the system does
  not leak which IDs exist at other hospitals.
"""

from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.utils.auth import get_current_user


# ── Type guards (use as FastAPI dependencies) ─────────────────────────────────

def require_hospital(current_user: dict = Depends(get_current_user)) -> str:
    """Return the caller's hospital_id, or raise 403 if caller is not a hospital."""
    if current_user.get("type") != "hospital":
        raise HTTPException(status_code=403, detail="Hospital access required")
    return current_user["user_id"]


def require_patient(current_user: dict = Depends(get_current_user)) -> str:
    """Return the caller's patient_id, or raise 403 if caller is not a patient."""
    if current_user.get("type") != "patient":
        raise HTTPException(status_code=403, detail="Patient access required")
    return current_user["user_id"]


# ── Scoped object loaders (call inline — need path param + db) ────────────────

def get_patient_scoped(patient_id: UUID, current_user: dict, db: Session):
    """
    Load a patient and enforce caller-specific scope rules.

    - Patient caller: allowed only if patient.id == caller's own id.
    - Hospital caller: allowed only if patient.hospital_id == caller's hospital.
    - Out-of-scope hospital request → 404 (not 403) to avoid ID leakage.
    - Soft-deleted patients (is_active=False) are treated as non-existent.
    """
    from app.models.patient import Patient

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    caller_type = current_user.get("type")
    caller_id = current_user.get("user_id")

    if caller_type == "patient":
        if str(patient.id) != caller_id:
            raise HTTPException(status_code=403, detail="Access denied")
    elif caller_type == "hospital":
        if str(patient.hospital_id) != caller_id:
            raise HTTPException(status_code=404, detail="Patient not found")
    else:
        raise HTTPException(status_code=403, detail="Access denied")

    return patient


def get_personnel_owned(personnel_id: UUID, current_user: dict, db: Session):
    """
    Load a personnel record that the calling hospital owns.

    Raises 403 if caller is not a hospital; 404 if not found or owned by another hospital.
    """
    from app.models.personnel import Personnel

    if current_user.get("type") != "hospital":
        raise HTTPException(status_code=403, detail="Hospital access required")

    personnel = db.query(Personnel).filter(Personnel.id == personnel_id).first()
    if not personnel or str(personnel.hospital_id) != current_user.get("user_id"):
        raise HTTPException(status_code=404, detail="Personnel not found")

    return personnel
