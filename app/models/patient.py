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
    previous_loss = Column(Boolean, default=False)           # kept for back-compat; derived as (count > 0)
    previous_loss_count = Column(Integer, default=0)         # v2: number of prior losses (scored)
    previous_stillbirth = Column(Boolean, default=False)
    previous_caesarean = Column(Boolean, default=False)
    previous_preeclampsia = Column(Boolean, default=False)
    has_hypertension = Column(Boolean, default=False)
    has_diabetes = Column(Boolean, default=False)
    has_sickle_cell = Column(Boolean, default=False)
    has_hiv = Column(Boolean, default=False)
    has_severe_anaemia = Column(Boolean, default=False)
    multiple_pregnancy = Column(Boolean, default=False)
    late_anc_initiation = Column(Boolean, default=False)     # legacy; contributes 0 to v2 score
    no_prior_anc = Column(Boolean, default=False)            # legacy; contributes 0 to v2 score

    # v2 signup fields — collected but NOT scored
    gravidity = Column(Integer, nullable=True)                # context only (double-counts parity+losses)
    blood_group = Column(String, nullable=True)               # A+/A-/B+/B-/O+/O-/AB+/AB-/unknown
    distance_close_to_hospital = Column(Boolean, nullable=True)  # for emergency logic, not risk score
    rh_negative = Column(Boolean, default=False)             # derived from blood_group ending in "-"

    # Missed check-in tracking (for clinician escalation alerts)
    consecutive_missed_checkins = Column(Integer, default=0)  # resets to 0 on any inbound reply
    missed_checkin_flag = Column(Boolean, default=False)      # True once escalation threshold crossed

    # Risk output — set by system at signup
    risk_level = Column(String, nullable=True)  # low|medium|high
    risk_level_set_at = Column(DateTime(timezone=True), nullable=True)
    risk_level_set_by = Column(String, nullable=True)  # "system" | clinician UUID

    # Patient status
    status = Column(String, default="active")  # active|post_loss|delivered
    pending_loss_confirmation = Column(Boolean, default=False)  # True while awaiting chat confirmation

    # Opt-out status
    opt_out_status = Column(String, nullable=True)
    # null = receiving messages | "paused" = silenced for 7 days | "stopped" = indefinitely silenced

    paused_until = Column(DateTime(timezone=True), nullable=True)
    # set when patient sends PAUSE, cleared when STOP or RESUME received

    # Soft-delete flag — inactive patients are hidden from normal views
    # TODO: known wrinkle — a soft-deleted patient's unique phone stays reserved.
    # A future migration should either free the phone or use a partial unique index.
    is_active = Column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=None, nullable=False, default=datetime.utcnow)

    # Relationships
    pregnancies = relationship("Pregnancy", back_populates="patient")
    messages = relationship("Message", back_populates="patient")
    appointments = relationship("Appointment", back_populates="patient")
