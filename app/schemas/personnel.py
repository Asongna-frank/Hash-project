"""Pydantic schemas for Personnel CRUD."""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_ROLES = Literal["doctor", "midwife", "nurse", "admin"]


class PersonnelCreate(BaseModel):
    """A staff member managed by a hospital (contact record, not a login user)."""
    name: str = Field(..., examples=["Grace Achu"])
    phone: str = Field(..., examples=["+237678334455"])
    email: Optional[str] = Field(default=None, examples=["grace.achu@gh-douala.cm"])
    role: _ROLES = Field(default="admin", examples=["midwife"])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "name": "Grace Achu",
                "phone": "+237678334455",
                "email": "grace.achu@gh-douala.cm",
                "role": "midwife",
            }]
        }
    )


class PersonnelUpdate(BaseModel):
    """Editable personnel fields (all optional)."""
    name: Optional[str] = Field(default=None, examples=["Grace Achu Nkfusai"])
    phone: Optional[str] = Field(default=None, examples=["+237678334455"])
    email: Optional[str] = Field(default=None, examples=["grace.n@gh-douala.cm"])
    role: Optional[_ROLES] = Field(default=None, examples=["nurse"])

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"role": "nurse", "email": "grace.n@gh-douala.cm"}]}
    )


class PersonnelResponse(BaseModel):
    id: UUID = Field(..., examples=["a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"])
    hospital_id: UUID = Field(..., examples=["4f996b23-92d3-4587-857b-038903d4253d"])
    name: str = Field(..., examples=["Grace Achu"])
    phone: str = Field(..., examples=["+237678334455"])
    email: Optional[str] = Field(default=None, examples=["grace.achu@gh-douala.cm"])
    role: str = Field(..., examples=["midwife"])
    created_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])
    updated_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
                "hospital_id": "4f996b23-92d3-4587-857b-038903d4253d",
                "name": "Grace Achu",
                "phone": "+237678334455",
                "email": "grace.achu@gh-douala.cm",
                "role": "midwife",
                "created_at": "2026-06-04T10:30:00+01:00",
                "updated_at": "2026-06-04T10:30:00+01:00",
            }]
        },
    )
