"""Message schemas for API responses."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageResponse(BaseModel):
    """A stored chat/notification message."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80",
                "patient_id": "7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60",
                "direction": "in",
                "channel": "app",
                "content": "I have had mild lower back pain since yesterday.",
                "message_type": "chat",
                "triage_level": "low",
                "created_at": "2026-06-04T10:30:00+01:00",
            }]
        },
    )

    id: UUID = Field(..., examples=["d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80"])
    patient_id: UUID = Field(..., examples=["7c1e9b40-2a3d-4f81-9c6a-1b2c3d4e5f60"])
    direction: str = Field(..., examples=["in"])  # "in" | "out"
    channel: str = Field(..., examples=["app"])   # "app" | "sms"
    content: str = Field(..., examples=["I have had mild lower back pain since yesterday."])
    message_type: str = Field(..., examples=["chat"])  # chat|checkin|tip|reminder|crisis
    triage_level: Optional[str] = Field(default=None, examples=["low"])
    created_at: datetime = Field(..., examples=["2026-06-04T10:30:00+01:00"])
