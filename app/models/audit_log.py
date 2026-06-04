"""AuditLog model — append-only record of every PHI access/change (SRS requirement)."""

import uuid

from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.core.database import Base


class AuditLog(Base):
    """
    One row per state-changing action on protected data.

    actor_type/actor_id  — who did it ("hospital"|"patient"|"system" + their id)
    action               — e.g. "patient.create", "patient.update", "patient.delete",
                           "patient.risk_override", "patient.signup"
    target_type/target_id — what was acted on ("patient" + uuid)
    details              — optional JSON (e.g. {"phone_changed": true, "account_type": "choronko"})
    """

    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_type = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    action = Column(String, nullable=False, index=True)
    target_type = Column(String, nullable=False)
    target_id = Column(String, nullable=False, index=True)
    details = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
