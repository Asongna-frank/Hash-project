"""PatientNote model — clinician notes on a patient (SRS M8 per-patient view)."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class PatientNote(Base):
    """A dated clinical note. Personnel have no logins (one account per
    hospital), so author_name is a client-supplied signature, not an identity."""

    __tablename__ = "patient_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = Column(UUID(as_uuid=True), ForeignKey("hospitals.id"), nullable=False, index=True)

    text = Column(Text, nullable=False)
    author_name = Column(String, nullable=True)  # e.g. "Dr Elvira" — display only

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
