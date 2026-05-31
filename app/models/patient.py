"""Patient model using separate table (not inheritance)."""

import uuid
from datetime import datetime, date

from sqlalchemy import Column, DateTime, Date, String, Boolean, UUID, ForeignKey, Integer
from sqlalchemy.orm import relationship

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

    # =================================================================
    # CLINICAL QUESTIONNAIRE — Dr Elvira's fields (replaces M1 placeholders)
    # history_of_pregnancy_loss, history_of_smoking, known_chronic_conditions
    # have been replaced by the structured fields below.
    # =================================================================

    # Demographics
    age = Column(Integer, nullable=True)
    parity = Column(Integer, default=0)  # number of prior births
    language = Column(String, nullable=True)
    preferred_support = Column(String, default="none")  # none|faith|peer|counsellor

    # Clinical history (questionnaire answers)
    previous_loss = Column(Boolean, default=False)
    previous_stillbirth = Column(Boolean, default=False)
    previous_caesarean = Column(Boolean, default=False)
    previous_preeclampsia = Column(Boolean, default=False)
    has_hypertension = Column(Boolean, default=False)
    has_diabetes = Column(Boolean, default=False)
    has_sickle_cell = Column(Boolean, default=False)
    has_hiv = Column(Boolean, default=False)
    has_severe_anaemia = Column(Boolean, default=False)
    multiple_pregnancy = Column(Boolean, default=False)
    late_anc_initiation = Column(Boolean, default=False)
    no_prior_anc = Column(Boolean, default=False)

    # Risk output — set by system at signup
    risk_level = Column(String, nullable=True)  # low|medium|high
    risk_level_set_at = Column(DateTime(timezone=True), nullable=True)
    risk_level_set_by = Column(String, nullable=True)  # "system" | clinician UUID

    # Patient status
    status = Column(String, default="active")  # active|post_loss|delivered
    pending_loss_confirmation = Column(Boolean, default=False)  # True while awaiting chat confirmation

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=None, nullable=False, default=datetime.utcnow)

    # Relationships
    pregnancies = relationship("Pregnancy", back_populates="patient")
