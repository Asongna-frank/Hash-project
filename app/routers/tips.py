# app/routers/tips.py
"""Daily tip endpoints — used by the Flutter home card and chat session bootstrap."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.message import Message
from app.utils.access import require_patient

router = APIRouter(prefix="/tips", tags=["tips"])


@router.get(
    "/today",
    summary="Get today's daily tip",
    description=(
        "Patient-only. Returns today's personalized tip if the daily job has run, "
        "otherwise the most recent prior tip as a fallback (is_today=false), or "
        "null if the patient has never received one. Hospitals → 403."
    ),
)
def get_todays_tip(
    patient_id: str = Depends(require_patient),
    db: Session = Depends(get_db),
):
    """
    Return the current patient's most relevant tip.

    Resolution order:
      1. Today's tip (created on or after today's UTC midnight), if the daily
         job has already run.
      2. Fallback — the most recent tip from any previous day, so the home card
         is never empty just because the 07:00 UTC job hasn't fired yet today.
      3. null — only when the patient has never received any tip.

    `is_today` lets the client distinguish a fresh tip from a carried-over one
    (e.g. to show a "Today's tip" vs "Recent tip" label).

    Flutter home card: display content when non-null, show placeholder otherwise.
    Chat session bootstrap: call this on open to pre-pend the tip card at the
    top of the conversation thread.

    Response shape:
      { "tip": null }
      { "tip": { "id": "...", "content": "...", "created_at": "...",
                 "is_read": false, "is_today": true } }
    """
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    base_query = (
        db.query(Message)
        .filter(
            Message.patient_id == patient_id,
            Message.message_type == "tip",
            Message.direction == "out",
        )
        .order_by(Message.created_at.desc())
    )

    # 1. Prefer today's tip; 2. fall back to the most recent tip of any day.
    tip = base_query.filter(Message.created_at >= today_start).first()
    is_today = tip is not None
    if tip is None:
        tip = base_query.first()

    if tip is None:
        # First-day patient: she signed up AFTER today's 07:00 UTC job, so no
        # tip exists yet. Generate her first tip on demand instead of showing
        # an empty card for up to 24h. Idempotent (send_daily_tip skips if one
        # already exists) and fail-soft: on any error keep the empty card.
        from app.models.patient import Patient
        from app.services.tip_sender import send_daily_tip

        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if patient is not None:
            try:
                send_daily_tip(patient, db)
                tip = base_query.filter(Message.created_at >= today_start).first()
                is_today = tip is not None
            except Exception:  # noqa: BLE001 — never break the home screen
                import logging
                logging.getLogger(__name__).exception(
                    "On-demand first tip failed | patient=%s", patient_id
                )

    if tip is None:
        return {"tip": None}

    return {
        "tip": {
            "id": str(tip.id),
            "content": tip.content,
            "created_at": tip.created_at.isoformat(),
            "is_read": tip.is_read,
            "is_today": is_today,
        }
    }
