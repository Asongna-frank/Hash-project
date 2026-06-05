# app/models/appointment.py
import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id  = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    hospital_id = Column(UUID(as_uuid=True), ForeignKey("hospitals.id"), nullable=False)

    title = Column(String, nullable=False)
    notes = Column(Text, nullable=True)

    # Optional: the doctor/midwife the visit is with — shown on the app's
    # "Upcoming Visit" card (e.g. "Dr. Ategha Tsili").
    doctor_name = Column(String, nullable=True)

    appointment_datetime = Column(DateTime(timezone=True), nullable=False)

    # The moment the patient/clinician wants to be reminded.
    # Patient supplies this directly; hospital-created rows set it to
    # appointment_datetime − 30 minutes so the two alarms land at 1h and 30m
    # before the appointment.
    reminder_datetime = Column(DateTime(timezone=True), nullable=False)

    # "patient" | "hospital"
    created_by = Column(String, nullable=False, default="patient")

    # Alarm flags — one-shot guards so each alarm fires exactly once.
    # alarm_1: fires 30 min before reminder_datetime
    # alarm_2: fires at reminder_datetime
    alarm_1_sent      = Column(Boolean, nullable=False, default=False)
    alarm_2_sent      = Column(Boolean, nullable=False, default=False)
    confirmation_sent = Column(Boolean, nullable=False, default=False)  # immediate, hospital-created only

    is_deleted = Column(Boolean, nullable=False, default=False)  # soft-delete only

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    patient  = relationship("Patient",  back_populates="appointments")
    hospital = relationship("Hospital", back_populates="appointments")
