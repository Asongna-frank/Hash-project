"""Hospital model using separate table (not inheritance)."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, String, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Hospital(Base):
    """Hospital entity — standalone reference table."""

    __tablename__ = "hospitals"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Hospital identification
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)

    # Location
    gps_lat = Column(Float, nullable=True)
    gps_lng = Column(Float, nullable=True)
    address = Column(String, nullable=False)

    # Staff contact
    personnel_name = Column(String, nullable=False)
    personnel_contact = Column(String, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=None, nullable=False, default=datetime.utcnow)

    # Relationships
    appointments = relationship("Appointment", back_populates="hospital")
