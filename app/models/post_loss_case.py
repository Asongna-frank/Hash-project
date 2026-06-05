"""PostLossCase model — M9 state for the post-pregnancy-loss care track."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class PostLossCase(Base):
    """
    One row per post-loss activation (SRS Core Data: PostLossCase).
    Created ONLY by clinician activation (post_loss.activate_post_loss).
    Drives the paced cadence and the PHQ-2 mental-health watch, and feeds the
    dashboard's post-loss banner/timeline.
    """

    __tablename__ = "post_loss_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"),
                        nullable=False, unique=True, index=True)

    activated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    activated_by = Column(String, nullable=False)  # hospital/clinician id
    opener_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Paced cadence stage: day1 (opener only) -> 48h -> every3days -> weekly
    current_cadence = Column(String, nullable=False, default="day1")

    # Gentle PHQ-2 check (week 2): offered once, response (or silence) recorded.
    phq2_offered_at = Column(DateTime(timezone=True), nullable=True)
    phq2_response = Column(Text, nullable=True)  # patient's own words (English pivot)
    phq2_responded_at = Column(DateTime(timezone=True), nullable=True)

    notes = Column(Text, nullable=True)  # clinician notes on the case
