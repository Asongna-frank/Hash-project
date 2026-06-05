"""Hospital CRUD router."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.hospital import Hospital
from app.schemas.hospital import HospitalPublic, HospitalResponse, HospitalUpdate
from app.utils.access import require_hospital

router = APIRouter(tags=["hospitals"])


@router.get(
    "/hospitals",
    response_model=list[HospitalPublic],
    summary="List active hospitals (public)",
    description=(
        "Public, unauthenticated list of active hospitals — used by the patient "
        "signup flow to pick a hospital. Returns only id, name, address, and GPS; "
        "never phone, password, or personnel. Supports skip/limit pagination."
    ),
)
def list_hospitals(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """
    Public list of active hospitals — safe for unauthenticated callers.
    Never exposes phone, password, or personnel details.
    """
    return (
        db.query(Hospital)
        .filter(Hospital.is_active.is_(True))
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/hospitals/{hospital_id}/public",
    response_model=HospitalPublic,
    summary="Get one hospital (public)",
    description=(
        "Public, unauthenticated lookup of a single active hospital — used by "
        "the patient app to show the hospital name/location chip without "
        "fetching the whole list. Returns only id, name, address, and GPS. "
        "Unknown or inactive hospital → 404."
    ),
)
def get_hospital_public(
    hospital_id: UUID,
    db: Session = Depends(get_db),
):
    """Public single-hospital view (same fields as the public list)."""
    hospital = (
        db.query(Hospital)
        .filter(Hospital.id == hospital_id, Hospital.is_active.is_(True))
        .first()
    )
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


@router.get(
    "/hospitals/{hospital_id}",
    response_model=HospitalResponse,
    summary="Get own hospital profile",
    description=(
        "Returns the authenticated hospital's own full profile (including phone). "
        "Hospital auth required; a hospital can only view itself — another id "
        "returns 403, an inactive/unknown one returns 404."
    ),
)
def get_hospital(
    hospital_id: UUID,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """Return the authenticated hospital's own full profile."""
    if str(hospital_id) != caller_id:
        raise HTTPException(status_code=403, detail="You can only view your own hospital")

    hospital = db.query(Hospital).filter(Hospital.id == hospital_id).first()
    if not hospital or not hospital.is_active:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


@router.patch(
    "/hospitals/{hospital_id}",
    response_model=HospitalResponse,
    summary="Update own hospital profile",
    description=(
        "Edits the authenticated hospital's name, address, or GPS. Hospital auth "
        "required; can only edit itself (another id → 403, unknown/inactive → 404)."
    ),
)
def update_hospital(
    hospital_id: UUID,
    body: HospitalUpdate,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """Edit own hospital profile. Cannot edit another hospital's record."""
    if str(hospital_id) != caller_id:
        raise HTTPException(status_code=403, detail="You can only edit your own hospital")

    hospital = db.query(Hospital).filter(Hospital.id == hospital_id).first()
    if not hospital or not hospital.is_active:
        raise HTTPException(status_code=404, detail="Hospital not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(hospital, field, value)

    db.commit()
    db.refresh(hospital)
    return hospital


@router.delete(
    "/hospitals/{hospital_id}",
    status_code=204,
    summary="Soft-delete own hospital",
    description=(
        "Soft-deletes the authenticated hospital (sets is_active=False). It "
        "disappears from the public list, but the row and all linked records "
        "(patients, appointments) are kept for audit. Hospital auth required; "
        "can only delete itself (another id → 403)."
    ),
)
def delete_hospital(
    hospital_id: UUID,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """
    Soft-delete own hospital: sets is_active=False.
    The hospital disappears from GET /hospitals but the row and all linked
    records (patients, appointments) remain in the database.
    """
    if str(hospital_id) != caller_id:
        raise HTTPException(status_code=403, detail="You can only delete your own hospital")

    hospital = db.query(Hospital).filter(Hospital.id == hospital_id).first()
    if not hospital or not hospital.is_active:
        raise HTTPException(status_code=404, detail="Hospital not found")

    hospital.is_active = False
    db.commit()
