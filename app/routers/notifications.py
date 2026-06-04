# app/routers/notifications.py
"""
Notification polling for the app bell/banner.
GET /notifications/unread  -> unread outbound reminder/checkin/crisis messages
POST /notifications/acknowledge -> mark them read so the badge clears
"""

from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field

from app.core.database import get_db
from app.models.message import Message
from app.utils.access import require_patient

router = APIRouter()


class NotificationItem(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [{
                "id": "e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091",
                "content": "Reminder: your antenatal check-up is tomorrow at 09:00.",
                "message_type": "reminder",
                "created_at": "2026-06-04T07:00:00+01:00",
            }]
        },
    )
    id: UUID = Field(..., examples=["e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091"])
    content: str = Field(..., examples=["Reminder: your antenatal check-up is tomorrow at 09:00."])
    message_type: str = Field(..., examples=["reminder"])  # reminder|checkin|crisis
    created_at: datetime = Field(..., examples=["2026-06-04T07:00:00+01:00"])


class AcknowledgeRequest(BaseModel):
    message_ids: list[UUID] = Field(
        ..., examples=[["e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091"]]
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"message_ids": ["e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091"]}]
        }
    )


@router.get(
    "/unread",
    response_model=list[NotificationItem],
    summary="List unread notifications",
    description=(
        "Patient-only. Returns the patient's unread outbound reminder/checkin/"
        "crisis messages (newest first) for the app bell/banner. Hospitals → 403."
    ),
)
def get_unread_notifications(
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    return db.query(Message).filter(
        Message.patient_id == patient_id,
        Message.direction == "out",
        Message.message_type.in_(["reminder", "checkin", "crisis"]),
        Message.is_read == False,
    ).order_by(Message.created_at.desc()).all()


@router.post(
    "/acknowledge",
    status_code=200,
    summary="Mark notifications read",
    description=(
        "Patient-only. Marks the listed messages as read so the badge clears. "
        "Only the caller's own messages are affected; the response lists the ids "
        "actually acknowledged. Hospitals → 403."
    ),
)
def acknowledge_notifications(
    body: AcknowledgeRequest,
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    acknowledged = []
    for msg_id in body.message_ids:
        msg = db.query(Message).filter(
            Message.id == msg_id,
            Message.patient_id == patient_id,
        ).first()
        if msg:
            msg.is_read = True
            acknowledged.append(str(msg_id))

    db.commit()
    return {"acknowledged": acknowledged}
