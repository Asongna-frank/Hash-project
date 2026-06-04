# app/routers/sms.py
"""
Inbound SMS channel — webhook transport for choronko (feature-phone) patients.

PUBLIC endpoint (called by the SMS provider, NOT JWT-authenticated). Like the
app chat endpoint, this is THIN: it verifies + parses the provider payload,
resolves the patient by phone, guards idempotency, then hands off to the SAME
shared brain (chat_core.process_message, channel="sms") and sends the reply
back out via sms_service.

The whole flow is built; only the provider adapter (StubInboundSMSParser) is
unimplemented, so the channel is "ready except the provider adapter."
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.message import Message
from app.models.patient import Patient
from app.services.chat_core import process_message
from app.services.sms_service import inbound_sms_parser, sms_service
from app.utils.phone import normalize_phone

router = APIRouter()
logger = logging.getLogger(__name__)


def _lookup_patient_by_phone(phone_e164: str, db: Session) -> Patient | None:
    """Resolve an active patient by their normalized E.164 phone."""
    return (
        db.query(Patient)
        .filter(Patient.phone == phone_e164, Patient.is_active.is_(True))
        .first()
    )


def _is_duplicate(provider_message_id: str | None, db: Session) -> bool:
    """Idempotency guard — True if this provider message was already processed."""
    if not provider_message_id:
        return False
    return (
        db.query(Message)
        .filter(Message.provider_message_id == provider_message_id)
        .first()
    ) is not None


@router.post(
    "/inbound",
    summary="Inbound SMS webhook (provider)",
    description=(
        "PUBLIC webhook called by the SMS provider (not JWT). Verifies the "
        "provider signature, resolves the patient by normalized phone, guards "
        "idempotency by provider_message_id, runs the shared chat brain "
        "(channel='sms'), and replies via SMS. The provider adapter is currently "
        "stubbed, so this returns 503 until one is wired in."
    ),
)
async def sms_inbound(request: Request, db: Session = Depends(get_db)):
    """
    Provider webhook for inbound choronko SMS.

    1. Verify signature + parse payload (provider adapter).
    2. Normalize sender phone to E.164 and resolve the patient.
    3. Idempotency: drop duplicates by provider_message_id.
    4. Run the shared brain (channel="sms").
    5. Send the reply back via sms_service.
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    # 1. Verify + parse. The provider adapter is stubbed → handle cleanly, no crash.
    try:
        inbound = inbound_sms_parser.verify_and_parse(headers, raw_body)
    except NotImplementedError as exc:
        logger.error("Inbound SMS provider not configured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Inbound SMS provider not configured",
        )
    except Exception as exc:  # noqa: BLE001 — invalid signature / bad payload
        logger.warning("Inbound SMS rejected (verify/parse failed): %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook")

    # 2. Normalize sender → E.164 BEFORE lookup (must match stored E.164 numbers).
    try:
        phone_e164 = normalize_phone(inbound.from_phone)
    except ValueError:
        logger.warning("Inbound SMS from unparseable number — ignoring")
        return {"status": "ignored"}

    patient = _lookup_patient_by_phone(phone_e164, db)
    if patient is None:
        # Do not leak existence; optionally tell the sender they aren't registered.
        logger.info("Inbound SMS from unregistered number — ignoring")
        return {"status": "ignored"}

    # 3. Idempotency
    if _is_duplicate(inbound.provider_message_id, db):
        logger.info("Inbound SMS duplicate (provider_message_id=%s) — no-op",
                    inbound.provider_message_id)
        return {"status": "duplicate"}

    # 4. Shared brain
    reply = process_message(
        patient, inbound.text, channel="sms", db=db,
        provider_message_id=inbound.provider_message_id,
    )

    # 5. Send the reply back out via the originating channel (SMS)
    sms_service.send_sms(to=patient.phone, message=reply.text)

    return {"status": "ok", "is_crisis": reply.is_crisis, "triage_level": reply.triage_level}
