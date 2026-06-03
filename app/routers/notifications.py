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
from pydantic import BaseModel, ConfigDict

from app.core.database import get_db
from app.models.message import Message
from app.utils.auth import get_current_user

router = APIRouter()


class NotificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    content: str
    message_type: str
    created_at: datetime


class AcknowledgeRequest(BaseModel):
    message_ids: list[UUID]


@router.get("/unread", response_model=list[NotificationItem])
def get_unread_notifications(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["type"] != "patient":
        raise HTTPException(status_code=403, detail="Patients only")

    return db.query(Message).filter(
        Message.patient_id == current_user["user_id"],
        Message.direction == "out",
        Message.message_type.in_(["reminder", "checkin", "crisis"]),
        Message.is_read == False,
    ).order_by(Message.created_at.desc()).all()


@router.post("/acknowledge", status_code=200)
def acknowledge_notifications(
    body: AcknowledgeRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["type"] != "patient":
        raise HTTPException(status_code=403, detail="Patients only")

    acknowledged = []
    for msg_id in body.message_ids:
        msg = db.query(Message).filter(
            Message.id == msg_id,
            Message.patient_id == current_user["user_id"],
        ).first()
        if msg:
            msg.is_read = True
            acknowledged.append(str(msg_id))

    db.commit()
    return {"acknowledged": acknowledged}
