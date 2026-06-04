# app/routers/chat.py
"""
Chat router — APP channel (HTTP transport) for patient messages.

This endpoint is a THIN wrapper: it does transport + identity only (resolve the
patient from the JWT), then hands off to the shared, channel-agnostic brain in
app/services/chat_core.py. All conversation logic — translation pivot, loss
detection, triage, opt-out, alerting — lives in the brain so the app and SMS
channels share exactly one code path (no care drift).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.patient import Patient
from app.schemas.common import ChatMessageRequest, ChatMessageResponse
from app.services.chat_core import process_message
from app.utils.access import require_patient

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_patient(patient_id: str, db: Session) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.post(
    "/message",
    response_model=ChatMessageResponse,
    summary="Send a chat message (app channel)",
    description=(
        "Patient-only entry point for app-channel messages. Resolves the patient "
        "from the JWT and hands off to the shared chat brain: multilingual pivot, "
        "PAUSE/STOP/RESUME, pregnancy-loss detection, and per-message triage "
        "(low/medium/high). Returns the reply plus triage_level, loss_detected, "
        "and is_crisis (true only for a pre-approved stored crisis message)."
    ),
)
def receive_message(
    body: ChatMessageRequest,
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    """
    Entry point for app-channel patient messages.

    Transport + identity only — the brain does the rest.
    """
    patient = _get_patient(patient_id, db)

    reply = process_message(patient, body.message, channel="app", db=db)

    return ChatMessageResponse(
        reply=reply.text,
        triage_level=reply.triage_level or "low",
        loss_detected=reply.loss_detected,
        is_crisis=reply.is_crisis,
    )
