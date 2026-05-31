"""Pydantic schemas for Hospital signup and response."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class HospitalCreate(BaseModel):
    """Schema for hospital signup request."""

    name: str
    phone: str
    password: str
    gps_lat: float | None = None
    gps_lng: float | None = None
    address: str
    personnel_name: str
    personnel_contact: str


class HospitalPublic(BaseModel):
    """Schema for public hospital list (no sensitive data)."""

    id: UUID
    name: str
    address: str
    gps_lat: float | None = None
    gps_lng: float | None = None

    model_config = ConfigDict(from_attributes=True)


class HospitalResponse(BaseModel):
    """Schema for hospital response (excludes hashed_password)."""

    id: UUID
    name: str
    phone: str
    address: str
    gps_lat: float | None = None
    gps_lng: float | None = None
    personnel_name: str
    personnel_contact: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
