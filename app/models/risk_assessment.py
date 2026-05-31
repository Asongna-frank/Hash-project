# app/models/risk_assessment.py
"""Risk assessment model for audit trail of risk level decisions."""

import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.core.database import Base


class RiskAssessment(Base):
    """Risk assessment record — audit trail for every risk level decision."""

    __tablename__ = "risk_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())
    computed_by = Column(String, nullable=False)  # "system" or clinician UUID string
    inputs = Column(JSONB, nullable=False)  # questionnaire answers or override reason
    rubric_version = Column(String, nullable=False)
    result_level = Column(String, nullable=False)  # low|medium|high
    score = Column(Integer, nullable=True)  # raw point total (null for clinician overrides)
