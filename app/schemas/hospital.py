"""Pydantic schemas for Hospital signup, CRUD, and public responses."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FirstPersonnel(BaseModel):
    """The first staff contact created together with the hospital at signup."""
    name: str = Field(..., examples=["Dr Elvira Mbappe"])
    phone: str = Field(..., examples=["+237677001122"])
    email: Optional[str] = Field(default=None, examples=["elvira.mbappe@gh-douala.cm"])
    role: str = Field(default="admin", examples=["doctor"])  # doctor|midwife|nurse|admin


class HospitalCreate(BaseModel):
    """Registration payload for a new hospital + its first personnel record."""
    name: str = Field(..., examples=["General Hospital Douala"])
    phone: str = Field(..., examples=["+237233420000"])
    password: str = Field(..., examples=["StrongPass123!"])
    gps_lat: Optional[float] = Field(default=None, examples=[4.0511])
    gps_lng: Optional[float] = Field(default=None, examples=[9.7679])
    address: str = Field(..., examples=["Boulevard de la Liberté, Akwa, Douala"])
    first_personnel: FirstPersonnel

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "name": "General Hospital Douala",
                "phone": "+237233420000",
                "password": "StrongPass123!",
                "gps_lat": 4.0511,
                "gps_lng": 9.7679,
                "address": "Boulevard de la Liberté, Akwa, Douala",
                "first_personnel": {
                    "name": "Dr Elvira Mbappe",
                    "phone": "+237677001122",
                    "email": "elvira.mbappe@gh-douala.cm",
                    "role": "doctor",
                },
            }]
        }
    )


class HospitalUpdate(BaseModel):
    """Editable hospital profile fields (all optional)."""
    name: Optional[str] = Field(default=None, examples=["General Hospital Douala (Akwa)"])
    gps_lat: Optional[float] = Field(default=None, examples=[4.0511])
    gps_lng: Optional[float] = Field(default=None, examples=[9.7679])
    address: Optional[str] = Field(default=None, examples=["New Bell, Douala"])

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"address": "New Bell, Douala"}]}
    )


class HospitalPublic(BaseModel):
    """Public-facing minimal view — safe for unauthenticated callers."""
    id: UUID = Field(..., examples=["4f996b23-92d3-4587-857b-038903d4253d"])
    name: str = Field(..., examples=["General Hospital Douala"])
    address: str = Field(..., examples=["Boulevard de la Liberté, Akwa, Douala"])
    gps_lat: Optional[float] = Field(default=None, examples=[4.0511])
    gps_lng: Optional[float] = Field(default=None, examples=[9.7679])

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "4f996b23-92d3-4587-857b-038903d4253d",
                "name": "General Hospital Douala",
                "address": "Boulevard de la Liberté, Akwa, Douala",
                "gps_lat": 4.0511,
                "gps_lng": 9.7679,
            }]
        },
    )


class HospitalResponse(BaseModel):
    """Full hospital profile returned to the authenticated hospital itself."""
    id: UUID = Field(..., examples=["4f996b23-92d3-4587-857b-038903d4253d"])
    name: str = Field(..., examples=["General Hospital Douala"])
    phone: str = Field(..., examples=["+237233420000"])
    address: str = Field(..., examples=["Boulevard de la Liberté, Akwa, Douala"])
    gps_lat: Optional[float] = Field(default=None, examples=[4.0511])
    gps_lng: Optional[float] = Field(default=None, examples=[9.7679])
    is_active: bool = Field(..., examples=[True])
    created_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "4f996b23-92d3-4587-857b-038903d4253d",
                "name": "General Hospital Douala",
                "phone": "+237233420000",
                "address": "Boulevard de la Liberté, Akwa, Douala",
                "gps_lat": 4.0511,
                "gps_lng": 9.7679,
                "is_active": True,
                "created_at": "2026-06-04T10:30:00+01:00",
            }]
        },
    )
