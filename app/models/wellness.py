"""DailyWellness model — per-day self-tracked hydration and sleep (Home card)."""

import uuid

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class DailyWellness(Base):
    """One row per patient per day. Upserted by the app's wellness card."""

    __tablename__ = "daily_wellness"
    __table_args__ = (
        UniqueConstraint("patient_id", "date", name="uq_wellness_patient_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)

    hydration_glasses = Column(Integer, nullable=True)  # 0..30
    sleep_hours = Column(Float, nullable=True)          # 0..24

    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
