# app/schemas/appointment.py
from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class AppointmentCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    notes: Optional[str] = None
    appointment_datetime: datetime = Field(
        ...,
        description="ISO 8601 datetime with timezone, e.g. 2026-07-15T09:00:00+01:00",
    )
    # hospital_id is NOT submitted — backend reads it from the patient's profile.


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    hospital_id: UUID
    title: str
    notes: Optional[str]
    appointment_datetime: datetime
    reminder_24h_sent: bool
    reminder_2h_sent: bool
    created_at: datetime


class AppointmentDeleteRequest(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, description="Appointment UUIDs to delete")
