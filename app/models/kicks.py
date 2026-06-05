"""KickCount model — daily fetal-movement (kick) counting, third trimester."""

import uuid

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class KickCount(Base):
    """One row per patient per day. The app increments as she counts."""

    __tablename__ = "kick_counts"
    __table_args__ = (
        UniqueConstraint("patient_id", "date", name="uq_kicks_patient_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)

    count = Column(Integer, nullable=False, default=0)            # movements felt today
    duration_minutes = Column(Integer, nullable=True)             # optional session length

    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
