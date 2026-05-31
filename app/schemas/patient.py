"""Pydantic schemas for Patient signup and response."""

from datetime import datetime, date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    """Schema for patient signup request."""

    name: str
    phone: str
    password: str
    hospital_id: UUID
    weeks_pregnant_at_signup: int = Field(ge=1, le=42)
    history_of_pregnancy_loss: bool = False
    history_of_smoking: bool = False
    known_chronic_conditions: str | None = None


class PatientResponse(BaseModel):
    """Schema for patient response (excludes hashed_password)."""

    id: UUID
    name: str
    phone: str
    hospital_id: UUID
    weeks_pregnant_at_signup: int
    lmp: date
    edd: date
    account_type: str
    history_of_pregnancy_loss: bool
    history_of_smoking: bool
    known_chronic_conditions: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
