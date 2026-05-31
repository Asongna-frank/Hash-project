# app/models/pregnancy.py
"""Pregnancy model for tracking pregnancy outcomes and loss detection."""

import uuid
from sqlalchemy import Column, String, Date, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Pregnancy(Base):
    """Pregnancy record — tracks outcomes, loss date, and routine pause status."""

    __tablename__ = "pregnancies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    lmp = Column(Date, nullable=False)
    edd = Column(Date, nullable=False)
    outcome = Column(String, default="ongoing")  # ongoing|live_birth|loss
    loss_date = Column(Date, nullable=True)
    ga_at_loss = Column(Integer, nullable=True)  # gestational week at loss
    routine_paused = Column(Boolean, default=False)  # True when loss confirmed — stops reminders
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    patient = relationship("Patient", back_populates="pregnancies")
