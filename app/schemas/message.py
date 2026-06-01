"""Message schemas for API responses."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MessageResponse(BaseModel):
    """Response schema for a Message ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    direction: str
    channel: str
    content: str
    message_type: str
    triage_level: Optional[str]
    created_at: datetime
