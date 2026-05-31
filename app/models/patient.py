"""Patient model using separate table (not inheritance)."""

import uuid
from datetime import datetime, date

from sqlalchemy import Column, DateTime, Date, String, Boolean, UUID, ForeignKey, Integer

from app.core.database import Base


class Patient(Base):
    """Patient entity — separate table linked to hospital."""

    __tablename__ = "patients"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Patient identification
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)

    # Hospital link
    hospital_id = Column(UUID(as_uuid=True), ForeignKey("hospitals.id"), nullable=False)

    # Pregnancy tracking
    weeks_pregnant_at_signup = Column(Integer, nullable=False)
    lmp = Column(Date, nullable=False)
    edd = Column(Date, nullable=False)

    # Account type
    account_type = Column(String, nullable=False, default="smartphone")

    # TODO: Replace with Dr Elvira's clinical questionnaire
    # These three columns are temporary placeholders for the MVP.
    # The actual questions and risk-scoring rubric will be defined by the
    # clinical lead and implemented as part of M2 (Patient & Pregnancy Profile).
    history_of_pregnancy_loss = Column(Boolean, nullable=False, default=False)
    history_of_smoking = Column(Boolean, nullable=False, default=False)
    known_chronic_conditions = Column(String, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=None, nullable=False, default=datetime.utcnow)
