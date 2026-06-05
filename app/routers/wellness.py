# app/routers/wellness.py
"""
Daily wellness self-tracking (Home card: hydration + sleep).

Mounted under /patients. A patient tracks only her own record; a hospital can
read (not write) its own patients' entries. One row per patient per day (UTC),
upserted on every POST so the card can save incrementally (tap a glass → POST).
"""

import logging
from datetime import date as date_type, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.wellness import DailyWellness
from app.utils.access import get_patient_scoped
from app.utils.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


class WellnessUpdate(BaseModel):
    """Partial upsert — send only what changed."""
    hydration_glasses: Optional[int] = Field(default=None, ge=0, le=30, examples=[4])
    sleep_hours: Optional[float] = Field(default=None, ge=0, le=24, examples=[7.5])

    model_config = ConfigDict(json_schema_extra={
        "examples": [{"hydration_glasses": 4, "sleep_hours": 7.5}]
    })


class WellnessResponse(BaseModel):
    date: date_type = Field(..., examples=["2026-06-05"])
    hydration_glasses: Optional[int] = Field(default=None, examples=[4])
    sleep_hours: Optional[float] = Field(default=None, examples=[7.5])
    updated_at: Optional[datetime] = Field(default=None, examples=["2026-06-05T10:30:00+00:00"])

    model_config = ConfigDict(from_attributes=True)


def _today() -> date_type:
    return datetime.now(timezone.utc).date()


@router.get(
    "/{patient_id}/wellness",
    response_model=WellnessResponse,
    summary="Get today's wellness entry",
    description=(
        "Returns the patient's wellness entry for today (UTC), or an empty "
        "record (null values) if nothing has been tracked yet — so the Home "
        "card can always render. Pass ?date=YYYY-MM-DD for a past day. A "
        "patient reads only her own; a hospital its own patients (else 404)."
    ),
)
def get_wellness(
    patient_id: UUID,
    date: Optional[date_type] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    patient = get_patient_scoped(patient_id, current_user, db)
    day = date or _today()
    row = (
        db.query(DailyWellness)
        .filter(DailyWellness.patient_id == patient.id, DailyWellness.date == day)
        .first()
    )
    if row:
        return row
    return WellnessResponse(date=day)  # empty card state


@router.post(
    "/{patient_id}/wellness",
    response_model=WellnessResponse,
    summary="Upsert today's wellness entry",
    description=(
        "Patient-only, own record only. Partial upsert of today's (UTC) entry: "
        "send only the field that changed (e.g. {\"hydration_glasses\": 5} when "
        "a glass is tapped). Values are clamped by validation (0–30 glasses, "
        "0–24 hours). Returns the full entry after the update."
    ),
)
def upsert_wellness(
    patient_id: UUID,
    body: WellnessUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    patient = get_patient_scoped(patient_id, current_user, db)
    if current_user.get("type") != "patient":
        raise HTTPException(status_code=403, detail="Patients only")

    if body.hydration_glasses is None and body.sleep_hours is None:
        raise HTTPException(status_code=422, detail="Send at least one field")

    day = _today()
    row = (
        db.query(DailyWellness)
        .filter(DailyWellness.patient_id == patient.id, DailyWellness.date == day)
        .first()
    )
    if row is None:
        row = DailyWellness(patient_id=patient.id, date=day)
        db.add(row)

    if body.hydration_glasses is not None:
        row.hydration_glasses = body.hydration_glasses
    if body.sleep_hours is not None:
        row.sleep_hours = body.sleep_hours

    db.commit()
    db.refresh(row)
    return row
