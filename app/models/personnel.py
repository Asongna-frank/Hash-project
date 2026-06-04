"""Personnel model — managed records linked to a hospital (not login users)."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Personnel(Base):
    __tablename__ = "personnel"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(UUID(as_uuid=True), ForeignKey("hospitals.id"), nullable=False)

    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)  # contact only — not a login identifier
    email = Column(String, nullable=True)
    role = Column(String, nullable=False, default="admin")  # doctor|midwife|nurse|admin

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    hospital = relationship("Hospital", back_populates="personnel")
