"""Personnel CRUD — hospital-only, own-hospital scoped."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.personnel import Personnel
from app.schemas.personnel import PersonnelCreate, PersonnelResponse, PersonnelUpdate
from app.utils.access import get_personnel_owned, require_hospital
from app.utils.auth import get_current_user

router = APIRouter(tags=["personnel"])


@router.post("/hospitals/{hospital_id}/personnel",
             response_model=PersonnelResponse, status_code=201)
def add_personnel(
    hospital_id: UUID,
    body: PersonnelCreate,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """Add a personnel member to the calling hospital. hospital_id in path must match caller."""
    if str(hospital_id) != caller_id:
        raise HTTPException(status_code=403,
                            detail="You can only add personnel to your own hospital")

    personnel = Personnel(
        hospital_id=hospital_id,
        name=body.name,
        phone=body.phone,
        email=body.email,
        role=body.role,
    )
    db.add(personnel)
    db.commit()
    db.refresh(personnel)
    return personnel


@router.get("/hospitals/{hospital_id}/personnel",
            response_model=list[PersonnelResponse])
def list_personnel(
    hospital_id: UUID,
    db: Session = Depends(get_db),
    caller_id: str = Depends(require_hospital),
):
    """List all personnel belonging to the calling hospital."""
    if str(hospital_id) != caller_id:
        raise HTTPException(status_code=403,
                            detail="You can only list personnel at your own hospital")

    return (
        db.query(Personnel)
        .filter(Personnel.hospital_id == hospital_id)
        .order_by(Personnel.created_at)
        .all()
    )


@router.patch("/personnel/{personnel_id}", response_model=PersonnelResponse)
def update_personnel(
    personnel_id: UUID,
    body: PersonnelUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update a personnel member. Caller must own the record (same hospital)."""
    personnel = get_personnel_owned(personnel_id, current_user, db)

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(personnel, field, value)

    db.commit()
    db.refresh(personnel)
    return personnel


@router.delete("/personnel/{personnel_id}", status_code=204)
def delete_personnel(
    personnel_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Hard-delete a personnel record. Row is permanently removed from the database."""
    personnel = get_personnel_owned(personnel_id, current_user, db)
    db.delete(personnel)
    db.commit()
