# app/schemas/appointment.py
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppointmentCreate(BaseModel):
    """Patient-created appointment — patient supplies reminder_datetime directly."""
    title: str = Field(..., min_length=2, max_length=200, examples=["Antenatal check-up"])
    notes: Optional[str] = Field(default=None, examples=["Bring previous scan results and ID card."])
    appointment_datetime: datetime = Field(
        ..., examples=["2026-07-15T09:00:00+01:00"],
        description="ISO 8601 with timezone, e.g. 2026-07-15T09:00:00+01:00",
    )
    reminder_datetime: datetime = Field(
        ..., examples=["2026-07-15T08:00:00+01:00"],
        description="When you want to be reminded — must be in the future and "
                    "on or before the appointment.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "title": "Antenatal check-up",
                "notes": "Bring previous scan results and ID card.",
                "appointment_datetime": "2026-07-15T09:00:00+01:00",
                "reminder_datetime": "2026-07-15T08:00:00+01:00",
            }]
        }
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
    """Hospital-created appointment for a patient (looked up by phone)."""
    patient_phone: str = Field(..., examples=["+237679977660"],
                               description="E.164 phone of the patient")
    title: str = Field(..., min_length=2, max_length=200, examples=["Growth scan (28 weeks)"])
    notes: Optional[str] = Field(default=None, examples=["Fasting not required."])
    appointment_datetime: datetime = Field(
        ..., examples=["2026-07-20T11:30:00+01:00"], description="ISO 8601 with timezone"
    )
    # reminder_datetime is NOT submitted — system sets it to appointment − 30min.

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "patient_phone": "+237679977660",
                "title": "Growth scan (28 weeks)",
                "notes": "Fasting not required.",
                "appointment_datetime": "2026-07-20T11:30:00+01:00",
            }]
        }
    )

    @model_validator(mode="after")
    def validate_future(self) -> "HospitalAppointmentCreate":
        appt = self.appointment_datetime
        if appt.tzinfo is None:
            appt = appt.replace(tzinfo=timezone.utc)
        if appt <= datetime.now(timezone.utc):
            raise ValueError("appointment_datetime must be in the future")
        return self


class AppointmentUpdate(BaseModel):
    """Editable appointment fields (all optional)."""
    title: Optional[str] = Field(default=None, min_length=2, max_length=200,
                                 examples=["Antenatal check-up (rescheduled)"])
    notes: Optional[str] = Field(default=None, examples=["Rescheduled at patient request."])
    appointment_datetime: Optional[datetime] = Field(default=None, examples=["2026-07-18T09:00:00+01:00"])
    reminder_datetime: Optional[datetime] = Field(default=None, examples=["2026-07-18T08:00:00+01:00"])

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"title": "Antenatal check-up (rescheduled)",
                                         "appointment_datetime": "2026-07-18T09:00:00+01:00"}]}
    )

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


_APPOINTMENT_EXAMPLE = {
    "id": "9d8c7b6a-5e4f-43a2-91b0-c1d2e3f4a5b6",
    "patient_id": "7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60",
    "hospital_id": "4f996b23-92d3-4587-857b-038903d4253d",
    "title": "Antenatal check-up",
    "notes": "Bring previous scan results and ID card.",
    "appointment_datetime": "2026-07-15T09:00:00+01:00",
    "reminder_datetime": "2026-07-15T08:00:00+01:00",
    "created_by": "patient",
    "alarm_1_sent": False,
    "alarm_2_sent": False,
    "confirmation_sent": False,
    "is_deleted": False,
    "created_at": "2026-06-04T10:30:00+01:00",
}


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [_APPOINTMENT_EXAMPLE]},
    )

    id: UUID = Field(..., examples=["9d8c7b6a-5e4f-43a2-91b0-c1d2e3f4a5b6"])
    patient_id: UUID = Field(..., examples=["7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60"])
    hospital_id: UUID = Field(..., examples=["4f996b23-92d3-4587-857b-038903d4253d"])
    title: str = Field(..., examples=["Antenatal check-up"])
    notes: Optional[str] = Field(default=None, examples=["Bring previous scan results and ID card."])
    appointment_datetime: datetime = Field(..., examples=["2026-07-15T09:00:00+01:00"])
    reminder_datetime: datetime = Field(..., examples=["2026-07-15T08:00:00+01:00"])
    created_by: str = Field(..., examples=["patient"])  # "patient" | "hospital"
    alarm_1_sent: bool = Field(..., examples=[False])
    alarm_2_sent: bool = Field(..., examples=[False])
    confirmation_sent: bool = Field(..., examples=[False])
    is_deleted: bool = Field(..., examples=[False])
    created_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])


class HospitalAppointmentResponse(AppointmentResponse):
    """Extends the base response with the immediate-confirmation SMS outcome."""
    sms_confirmation_ok: bool = Field(default=False, examples=[True])
    sms_confirmation_error: Optional[str] = Field(default=None, examples=[None])

    model_config = ConfigDict(
        from_attributes=False,
        json_schema_extra={
            "examples": [{
                **_APPOINTMENT_EXAMPLE,
                "created_by": "hospital",
                "confirmation_sent": True,
                "sms_confirmation_ok": True,
                "sms_confirmation_error": None,
            }]
        },
    )


class AppointmentDeleteRequest(BaseModel):
    """Bulk soft-delete by appointment id."""
    ids: list[UUID] = Field(
        ..., min_length=1,
        examples=[["9d8c7b6a-5e4f-43a2-91b0-c1d2e3f4a5b6",
                   "1a2b3c4d-5e6f-47a8-99b0-c1d2e3f4a5b6"]],
        description="Appointment UUIDs to delete",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"ids": ["9d8c7b6a-5e4f-43a2-91b0-c1d2e3f4a5b6",
                                  "1a2b3c4d-5e6f-47a8-99b0-c1d2e3f4a5b6"]}]
        }
    )
