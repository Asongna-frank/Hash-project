# app/routers/chat.py
"""
Chat router — entry point for all patient messages.

Pipeline (in order):
1.  Save inbound message to messages table
2.  Check PAUSE / STOP / RESUME keywords
3.  Check patient.status == "post_loss" (stub — M9 handles this fully)
4.  Handle pending_loss_confirmation from previous AMBIGUOUS detection
5.  Run loss detection (M2 — keyword + LLM)
6.  If NOT_A_LOSS → run M3 conversation engine (memory + Groq → reply + triage)
7.  Save outbound reply to messages table
8.  Return ChatMessageResponse
"""

import logging
from datetime import date, datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.risk_config import RUBRIC_VERSION
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.schemas.common import ChatMessageRequest, ChatMessageResponse
from app.services.loss_detection import detect_loss
from app.services.conversation import generate_reply
from app.services.message_store import save_inbound, save_outbound
from app.services.prompts import (
    LOSS_AMBIGUOUS_FOLLOWUP,
    POST_LOSS_OPENING_MESSAGE,
    PAUSE_CONFIRMATION,
    STOP_CONFIRMATION,
    RESUME_CONFIRMATION,
)
from app.utils.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

# Opt-out keywords — checked before any other logic
OPT_OUT_KEYWORDS = {
    "pause":  "paused",
    "stop":   "stopped",
    "resume": "resume",
}


def _get_patient(patient_id: str, db: Session) -> Patient:
    """Helper to fetch patient or raise 404."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def _get_pregnancy(patient_id, db: Session) -> Pregnancy | None:
    """Helper to fetch patient's most recent pregnancy."""
    return (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient_id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )


def _handle_opt_out(
    keyword: str,
    patient: Patient,
    db: Session,
) -> ChatMessageResponse:
    """
    Handle PAUSE / STOP / RESUME.
    Updates patient opt_out_status and returns a single confirmation.
    No further messages are sent after this return.
    """
    if keyword == "pause":
        patient.opt_out_status = "paused"
        patient.paused_until = datetime.now(timezone.utc) + timedelta(days=7)
        reply = PAUSE_CONFIRMATION

    elif keyword == "stop":
        patient.opt_out_status = "stopped"
        patient.paused_until = None
        reply = STOP_CONFIRMATION

    elif keyword == "resume":
        patient.opt_out_status = None
        patient.paused_until = None
        reply = RESUME_CONFIRMATION

    else:
        reply = RESUME_CONFIRMATION  # safe default

    db.commit()

    # Save the confirmation reply to message history
    out_msg = save_outbound(patient.id, reply, message_type="chat")
    db.add(out_msg)
    db.commit()

    return ChatMessageResponse(
        reply=reply,
        triage_level="low",
        loss_detected=False,
    )


def _handle_loss_confirmed(
    patient: Patient,
    pregnancy: Pregnancy | None,
    db: Session,
) -> ChatMessageResponse:
    """
    Apply all state changes when a pregnancy loss is confirmed.
    """
    current_ga_weeks = (date.today() - patient.lmp).days // 7

    patient.status = "post_loss"
    patient.pending_loss_confirmation = False

    if pregnancy:
        pregnancy.outcome = "loss"
        pregnancy.loss_date = date.today()
        pregnancy.ga_at_loss = current_ga_weeks
        pregnancy.routine_paused = True

    # Escalate risk level to high for physical recovery monitoring
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
        "Pregnancy loss confirmed | patient=%s | status → post_loss", patient.id
    )

    # TODO M6: send real-time alert to hospital dashboard
    # TODO M9: activate post-loss care track and paced cadence

    reply = POST_LOSS_OPENING_MESSAGE

    out_msg = save_outbound(
        patient.id, reply, message_type="crisis"
    )
    db.add(out_msg)
    db.commit()

    return ChatMessageResponse(
        reply=reply,
        triage_level="high",
        loss_detected=True,
    )


@router.post("/message", response_model=ChatMessageResponse)
def receive_message(
    body: ChatMessageRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Entry point for all patient messages.

    Full M3 pipeline: message storage, loss detection, conversation, triage.
    """
    # Patients only
    if current_user["type"] != "patient":
        raise HTTPException(status_code=403, detail="Patients only")

    patient = _get_patient(current_user["user_id"], db)
    pregnancy = _get_pregnancy(patient.id, db)
    message_text = body.message.strip()

    # ------------------------------------------------------------------
    # STEP 1 — Save inbound message immediately
    # triage_level will be updated after pipeline runs
    # ------------------------------------------------------------------
    in_msg = save_inbound(
        patient_id=patient.id,
        content=message_text,
        triage_level="low",   # placeholder — updated after triage below
    )
    db.add(in_msg)
    db.commit()
    db.refresh(in_msg)

    # ------------------------------------------------------------------
    # STEP 2 — PAUSE / STOP / RESUME check (before anything else)
    # ------------------------------------------------------------------
    lower = message_text.lower().strip()
    if lower in OPT_OUT_KEYWORDS:
        return _handle_opt_out(lower, patient, db)

    # ------------------------------------------------------------------
    # STEP 3 — Post-loss track (stub — M9 will handle fully)
    # ------------------------------------------------------------------
    if patient.status == "post_loss":
        # Run conversation engine in post-loss mode — same pipeline
        # M9 will add pacing, PHQ-2, and crisis route on top of this
        reply, triage_level = generate_reply(patient, message_text, db)

        in_msg.triage_level = triage_level
        out_msg = save_outbound(patient.id, reply)
        db.add(out_msg)
        db.commit()

        return ChatMessageResponse(
            reply=reply,
            triage_level=triage_level,
            loss_detected=True,   # flag so frontend keeps post-loss UI
        )

    # ------------------------------------------------------------------
    # STEP 4 — Handle pending loss confirmation from previous AMBIGUOUS
    # ------------------------------------------------------------------
    if patient.pending_loss_confirmation:
        affirmative = {"yes", "yeah", "yep", "oui", "yes i did", "yes i have"}
        negative = {"no", "non", "nope", "no i didn't", "no i haven't"}

        if any(a in lower for a in affirmative):
            return _handle_loss_confirmed(patient, pregnancy, db)

        elif any(n in lower for n in negative):
            patient.pending_loss_confirmation = False
            db.commit()
            # Fall through to normal conversation pipeline below

        else:
            # Still unclear — run loss detection again on this message
            result = detect_loss(message_text)
            if result == "CONFIRMED":
                return _handle_loss_confirmed(patient, pregnancy, db)
            elif result == "AMBIGUOUS":
                out_msg = save_outbound(
                    patient.id, LOSS_AMBIGUOUS_FOLLOWUP
                )
                db.add(out_msg)
                db.commit()
                return ChatMessageResponse(
                    reply=LOSS_AMBIGUOUS_FOLLOWUP,
                    triage_level="medium",
                    loss_detected=False,
                )
            # NOT_A_LOSS — clear flag and continue
            patient.pending_loss_confirmation = False
            db.commit()

    # ------------------------------------------------------------------
    # STEP 5 — Loss detection on normal messages
    # ------------------------------------------------------------------
    loss_result = detect_loss(message_text)

    if loss_result == "CONFIRMED":
        return _handle_loss_confirmed(patient, pregnancy, db)

    if loss_result == "AMBIGUOUS":
        patient.pending_loss_confirmation = True
        db.commit()

        out_msg = save_outbound(patient.id, LOSS_AMBIGUOUS_FOLLOWUP)
        db.add(out_msg)
        db.commit()

        return ChatMessageResponse(
            reply=LOSS_AMBIGUOUS_FOLLOWUP,
            triage_level="medium",
            loss_detected=False,
        )

    # ------------------------------------------------------------------
    # STEP 6 — Main conversation pipeline (NOT_A_LOSS path)
    # ------------------------------------------------------------------
    if patient.pending_loss_confirmation:
        patient.pending_loss_confirmation = False
        db.commit()

    reply, triage_level = generate_reply(patient, message_text, db)

    # Update inbound message with real triage level
    in_msg.triage_level = triage_level
    db.add(in_msg)

    # Save outbound reply
    out_msg = save_outbound(patient.id, reply)
    db.add(out_msg)
    db.commit()

    # TODO M6: if triage_level == "high" → alert hospital in real time

    return ChatMessageResponse(
        reply=reply,
        triage_level=triage_level,
        loss_detected=False,
    )