"""Pydantic schemas for Personnel CRUD."""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

_ROLES = Literal["doctor", "midwife", "nurse", "admin"]


class PersonnelCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    role: _ROLES = "admin"


class PersonnelUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[_ROLES] = None


class PersonnelResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    name: str
    phone: str
    email: Optional[str] = None
    role: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
