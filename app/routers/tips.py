# app/routers/tips.py
"""Daily tip endpoints — used by the Flutter home card and chat session bootstrap."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.message import Message
from app.utils.auth import get_current_user

router = APIRouter(prefix="/tips", tags=["tips"])


@router.get("/today")
def get_todays_tip(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the current patient's tip for today, or null if not yet delivered.

    Flutter home card: display content when non-null, show placeholder otherwise.
    Chat session bootstrap: call this on open to pre-pend the tip card at the
    top of the conversation thread.

    Response shape:
      { "tip": null }
      { "tip": { "id": "...", "content": "...", "created_at": "...", "is_read": false } }
    """
    if current_user["type"] != "patient":
        raise HTTPException(status_code=403, detail="Patients only.")

    patient_id = current_user["user_id"]
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    tip = (
        db.query(Message)
        .filter(
            Message.patient_id == patient_id,
            Message.message_type == "tip",
            Message.direction == "out",
            Message.created_at >= today_start,
        )
        .order_by(Message.created_at.desc())
        .first()
    )

    if tip is None:
        return {"tip": None}

    return {
        "tip": {
            "id": str(tip.id),
            "content": tip.content,
            "created_at": tip.created_at.isoformat(),
            "is_read": tip.is_read,
        }
    }
