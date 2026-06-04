"""Pydantic schemas for Hospital signup, CRUD, and public responses."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FirstPersonnel(BaseModel):
    """Nested personnel record created together with the hospital at signup."""
    name: str
    phone: str
    email: Optional[str] = None
    role: str = "admin"


class HospitalCreate(BaseModel):
    name: str
    phone: str
    password: str
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    address: str
    first_personnel: FirstPersonnel


class HospitalUpdate(BaseModel):
    name: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    address: Optional[str] = None


class HospitalPublic(BaseModel):
    """Public-facing minimal view — safe for unauthenticated callers."""
    id: UUID
    name: str
    address: str
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class HospitalResponse(BaseModel):
    """Full hospital profile returned to the authenticated hospital itself."""
    id: UUID
    name: str
    phone: str
    address: str
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
