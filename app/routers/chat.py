# app/routers/chat.py
import logging
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.core.risk_config import RUBRIC_VERSION
from app.schemas.common import ChatMessageRequest, ChatMessageResponse
from app.services.loss_detection import detect_loss
from app.services.prompts import (
    LOSS_AMBIGUOUS_FOLLOWUP,
    POST_LOSS_OPENING_MESSAGE,
    PLACEHOLDER_TRIAGE_REPLY,
)
from app.utils.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/message", response_model=ChatMessageResponse)
def receive_message(
    body: ChatMessageRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Entry point for all patient messages.

    M2 scope: loss detection only.
    TODO M4: extend with full Low/Medium/High message triage.
    TODO M5: replace placeholder replies with content library templates.
    TODO M6: add hospital alert on loss confirmed.
    TODO M9: activate post-loss care track on loss confirmed.
    """
    # Only patients can send chat messages
    if current_user["type"] != "patient":
        raise HTTPException(status_code=403, detail="Patients only")

    patient_id = current_user["user_id"]
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Fetch current pregnancy record
    pregnancy = (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient_id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )

    # ----------------------------------------------------------------
    # 1. Already in post-loss track — skip loss detection
    # ----------------------------------------------------------------
    if patient.status == "post_loss":
        # TODO M9: route to post-loss conversation handler
        return ChatMessageResponse(
            reply=PLACEHOLDER_TRIAGE_REPLY,
            loss_detected=True,
        )

    # ----------------------------------------------------------------
    # 2. Pending loss confirmation from previous AMBIGUOUS message
    # ----------------------------------------------------------------
    if patient.pending_loss_confirmation:
        msg_lower = body.message.lower().strip()
        affirmative = {"yes", "yeah", "yep", "oui", "yes i did", "yes i have"}
        negative = {"no", "non", "nope", "no i didn't", "no i haven't"}

        if any(a in msg_lower for a in affirmative):
            result = "CONFIRMED"
        elif any(n in msg_lower for n in negative):
            patient.pending_loss_confirmation = False
            db.commit()
            return ChatMessageResponse(
                reply=PLACEHOLDER_TRIAGE_REPLY,
                loss_detected=False,
            )
        else:
            # Still unclear — run detection again on this message
            result = detect_loss(body.message)
    else:
        # ----------------------------------------------------------------
        # 3. Normal message — run loss detection pipeline
        # ----------------------------------------------------------------
        result = detect_loss(body.message)

    # ----------------------------------------------------------------
    # 4. Act on detection result
    # ----------------------------------------------------------------
    if result == "CONFIRMED":
        current_ga_weeks = (date.today() - patient.lmp).days // 7

        # Update patient status
        patient.status = "post_loss"
        patient.pending_loss_confirmation = False

        # Update pregnancy record
        if pregnancy:
            pregnancy.outcome = "loss"
            pregnancy.loss_date = date.today()
            pregnancy.ga_at_loss = current_ga_weeks
            pregnancy.routine_paused = True

        # Escalate risk level to high if not already
        if patient.risk_level != "high":
            patient.risk_level = "high"
            patient.risk_level_set_at = datetime.now(timezone.utc)
            patient.risk_level_set_by = "system"

            risk_record = RiskAssessment(
                patient_id=patient.id,
                computed_by="system",
                inputs={
                    "reason": "Automatic escalation on confirmed pregnancy loss",
                    "loss_confirmed": True,
                },
                rubric_version=RUBRIC_VERSION,
                result_level="high",
                score=None,
            )
            db.add(risk_record)

        db.commit()

        logger.info(
            "Pregnancy loss confirmed for patient %s — status updated to post_loss",
            patient_id,
        )

        # TODO M6: send real-time alert to hospital dashboard
        # TODO M9: activate post-loss care track (paced cadence, opener sent)

        return ChatMessageResponse(
            reply=POST_LOSS_OPENING_MESSAGE,
            loss_detected=True,
        )

    elif result == "AMBIGUOUS":
        patient.pending_loss_confirmation = True
        db.commit()

        return ChatMessageResponse(
            reply=LOSS_AMBIGUOUS_FOLLOWUP,
            loss_detected=False,
        )

    else:
        # NOT_A_LOSS — clear any stale pending flag, route normally
        if patient.pending_loss_confirmation:
            patient.pending_loss_confirmation = False
            db.commit()

        # TODO M4: replace with full Low/Medium/High triage pipeline
        return ChatMessageResponse(
            reply=PLACEHOLDER_TRIAGE_REPLY,
            loss_detected=False,
        )