"""Alert model — M6 Emergency & Alerting (SRS section 2.5 / Core Data)."""

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Alert(Base):
    """
    One high-priority event the hospital must see: a High-acuity message, the
    emergency button, a missed-check-in escalation, or a post-loss crisis.
    """

    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = Column(UUID(as_uuid=True), ForeignKey("hospitals.id"), nullable=False, index=True)

    source = Column(String, nullable=False)
    # "message_triage" | "emergency_button" | "missed_checkins" | "post_loss_crisis"

    triage_level = Column(String, nullable=True)   # low|medium|high (high for most alerts)
    reason = Column(Text, nullable=False)          # human-readable one-liner for the dashboard
    context = Column(Text, nullable=True)          # recent conversation excerpt (SRS 2.5)

    # GPS — only set when the smartphone emergency button provided a location.
    # For choronko patients the dashboard falls back to the registered address.
    gps_lat = Column(Float, nullable=True)
    gps_lng = Column(Float, nullable=True)

    status = Column(String, nullable=False, default="new")  # new | ack | resolved
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient")
    hospital = relationship("Hospital")
