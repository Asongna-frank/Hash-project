"""Message model — stores all inbound and outbound chat messages."""

import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Message(Base):
    """Message entity — stores every inbound/outbound message for conversation memory."""

    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    direction = Column(String, nullable=False)
    # "in"  = message from patient to system
    # "out" = message from system to patient

    channel = Column(String, nullable=False, default="app")
    # "app" | "sms"

    content = Column(Text, nullable=False)
    # the full text of the message

    message_type = Column(String, nullable=False, default="chat")
    # "chat" | "checkin" | "tip" | "reminder" | "crisis"

    triage_level = Column(String, nullable=True)
    # null for outbound messages
    # "low" | "medium" | "high" for inbound messages

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    is_read = Column(Boolean, default=False, nullable=False)
    # False = unread notification | True = acknowledged by patient
    # Only meaningful for direction="out" reminder/checkin/crisis messages

    # Relationships
    patient = relationship("Patient", back_populates="messages")
