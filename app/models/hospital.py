"""Hospital model using separate table (not inheritance)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Hospital(Base):
    __tablename__ = "hospitals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False, index=True)
    # Optional second login identifier + contact address (SRS: hospital contact info)
    email = Column(String, unique=True, nullable=True, index=True)
    hashed_password = Column(String, nullable=False)

    gps_lat = Column(Float, nullable=True)
    gps_lng = Column(Float, nullable=True)
    address = Column(String, nullable=False)

    # Soft-delete flag — inactive hospitals are hidden from public listings
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # One hospital → many personnel (managed records, not login users)
    personnel = relationship("Personnel", back_populates="hospital",
                             cascade="all, delete-orphan")

    # Other relationships
    appointments = relationship("Appointment", back_populates="hospital")
