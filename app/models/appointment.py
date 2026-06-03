# app/models/appointment.py
import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id   = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    hospital_id  = Column(UUID(as_uuid=True), ForeignKey("hospitals.id"), nullable=False)

    title        = Column(String, nullable=False)   # e.g. "Antenatal check-up", "Scan"
    notes        = Column(Text, nullable=True)

    appointment_datetime = Column(DateTime(timezone=True), nullable=False)

    reminder_24h_sent = Column(Boolean, default=False)
    reminder_2h_sent  = Column(Boolean, default=False)

    is_deleted   = Column(Boolean, default=False)    # soft delete — never hard-delete

    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    patient  = relationship("Patient",  back_populates="appointments")
    hospital = relationship("Hospital", back_populates="appointments")
