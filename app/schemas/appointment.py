# app/schemas/appointment.py
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppointmentCreate(BaseModel):
    """Patient-created appointment — patient supplies reminder_datetime directly."""
    title: str = Field(..., min_length=2, max_length=200)
    notes: Optional[str] = None
    appointment_datetime: datetime = Field(
        ..., description="ISO 8601 with timezone, e.g. 2026-07-15T09:00:00+01:00"
    )
    reminder_datetime: datetime = Field(
        ..., description="When you want to be reminded — must be in the future and "
                         "on or before the appointment."
    )

    @model_validator(mode="after")
    def validate_datetimes(self) -> "AppointmentCreate":
        now = datetime.now(timezone.utc)
        appt = self.appointment_datetime
        reminder = self.reminder_datetime

        # Make comparison timezone-safe
        if appt.tzinfo is None:
            appt = appt.replace(tzinfo=timezone.utc)
        if reminder.tzinfo is None:
            reminder = reminder.replace(tzinfo=timezone.utc)

        if appt <= now:
            raise ValueError("appointment_datetime must be in the future")
        if reminder <= now:
            raise ValueError("reminder_datetime must be in the future")
        if reminder > appt:
            raise ValueError(
                "reminder_datetime must be on or before appointment_datetime"
            )
        return self


class HospitalAppointmentCreate(BaseModel):
    """Hospital-created appointment for a choronko patient (looked up by phone)."""
    patient_phone: str = Field(..., description="E.164 phone of the patient")
    title: str = Field(..., min_length=2, max_length=200)
    notes: Optional[str] = None
    appointment_datetime: datetime = Field(
        ..., description="ISO 8601 with timezone"
    )
    # reminder_datetime is NOT submitted — system sets it to appointment − 30min.

    @model_validator(mode="after")
    def validate_future(self) -> "HospitalAppointmentCreate":
        appt = self.appointment_datetime
        if appt.tzinfo is None:
            appt = appt.replace(tzinfo=timezone.utc)
        if appt <= datetime.now(timezone.utc):
            raise ValueError("appointment_datetime must be in the future")
        return self


class AppointmentUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=200)
    notes: Optional[str] = None
    appointment_datetime: Optional[datetime] = None
    reminder_datetime: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_if_present(self) -> "AppointmentUpdate":
        now = datetime.now(timezone.utc)
        appt = self.appointment_datetime
        reminder = self.reminder_datetime

        if appt is not None:
            if appt.tzinfo is None:
                appt = appt.replace(tzinfo=timezone.utc)
            if appt <= now:
                raise ValueError("appointment_datetime must be in the future")

        if reminder is not None:
            if reminder.tzinfo is None:
                reminder = reminder.replace(tzinfo=timezone.utc)
            if reminder <= now:
                raise ValueError("reminder_datetime must be in the future")
            if appt is not None and reminder > appt:
                raise ValueError(
                    "reminder_datetime must be on or before appointment_datetime"
                )
        return self


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    hospital_id: UUID
    title: str
    notes: Optional[str]
    appointment_datetime: datetime
    reminder_datetime: datetime
    created_by: str
    alarm_1_sent: bool
    alarm_2_sent: bool
    confirmation_sent: bool
    is_deleted: bool
    created_at: datetime


class HospitalAppointmentResponse(AppointmentResponse):
    """Extends the base response with the immediate-confirmation SMS outcome."""
    sms_confirmation_ok: bool = False
    sms_confirmation_error: Optional[str] = None

    model_config = ConfigDict(from_attributes=False)


class AppointmentDeleteRequest(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, description="Appointment UUIDs to delete")
