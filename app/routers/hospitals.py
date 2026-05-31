"""Hospitals router for hospital discovery."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.hospital import Hospital
from app.schemas.hospital import HospitalPublic

router = APIRouter(tags=["hospitals"])


@router.get("/hospitals", response_model=list[HospitalPublic])
def list_hospitals(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """
    Get a public list of hospitals.

    Returns hospital name, address, and GPS coordinates for patient discovery.
    Does not require authentication.

    # TODO Phase 2: Add location-based filtering (region, lat/lng + radius_km)
    # when hospital count grows across multiple countries.
    # The gps_lat and gps_lng columns are already stored for this purpose.

    Args:
        skip: Number of hospitals to skip (pagination).
        limit: Maximum number of hospitals to return.

    Returns:
        List of HospitalPublic objects (public-facing hospital data).
    """
    hospitals = db.query(Hospital).offset(skip).limit(limit).all()
    return hospitals
