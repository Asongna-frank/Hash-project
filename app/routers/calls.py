# app/routers/calls.py
"""
Voice calls — doctor calls a patient from the web portal (WebRTC).

POST /calls starts the ring; all subsequent signaling flows over the two
existing WebSockets (hospital /alerts/ws, patient /chat/ws). See the frame
protocol in app/services/call_service.py.
"""

import asyncio
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.patient import Patient
from app.services.call_service import (
    _push,
    calls,
    patient_manager,
    ring_timeout_watch,
)
from app.utils.access import require_hospital
from app.utils.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


class CallStart(BaseModel):
    patient_id: UUID = Field(..., examples=["0ba7cc33-630d-4f6d-9e5a-14433fc49c05"])

    model_config = ConfigDict(json_schema_extra={
        "examples": [{"patient_id": "0ba7cc33-630d-4f6d-9e5a-14433fc49c05"}]
    })


@router.post(
    "",
    status_code=201,
    summary="Start a voice call to a patient (hospital)",
    description=(
        "Hospital-only, own patients only (others → 404). Creates a call "
        "session and makes the patient's app ring: an incoming_call frame on "
        "her chat WebSocket plus a push notification. Returns the call_id and "
        "whether her socket is currently online (patient_online=false → she "
        "will likely miss it; the ring still times out after 45s into a "
        "missed-call message). All further signaling (answer, SDP, ICE, "
        "hang-up) flows over the two WebSockets — see /docs for the frames. "
        "409 if she already has a call in progress."
    ),
)
async def start_call(
    body: CallStart,
    db: Session = Depends(get_db),
    hospital_id: str = Depends(require_hospital),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == body.patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient or str(patient.hospital_id) != hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")
    if patient.account_type == "choronko":
        raise HTTPException(
            status_code=422,
            detail="Choronko patients have no app — call her phone directly",
        )

    hospital = db.query(Hospital).filter(Hospital.id == hospital_id).first()

    try:
        session = calls.create(hospital_id, str(patient.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Ring the app: WS frame (instant if online) + push (wakes a closed app)
    online = await patient_manager.send(str(patient.id), {
        "type": "incoming_call",
        "call_id": session.call_id,
        "hospital_name": hospital.name if hospital else "Your hospital",
    })
    await run_in_threadpool(
        _push, str(patient.id), "📞 Incoming call",
        f"{hospital.name if hospital else 'Your care team'} is calling you — open HASH to answer.",
    )

    asyncio.create_task(ring_timeout_watch(session.call_id))

    logger.info("Call started | call=%s | hospital=%s | patient=%s | online=%s",
                session.call_id, hospital_id, patient.id, online)
    return {
        "call_id": session.call_id,
        "patient_online": online,
        "ring_timeout_seconds": 45,
    }


@router.get(
    "/ice-config",
    summary="ICE servers for WebRTC (both roles)",
    description=(
        "Returns the STUN/TURN configuration both peers must pass to "
        "RTCPeerConnection. TURN entries appear only when configured on the "
        "server (recommended for reliability across mobile networks)."
    ),
)
def ice_config(current_user: dict = Depends(get_current_user)):
    ice: list[dict] = [{"urls": ["stun:stun.l.google.com:19302",
                                 "stun:stun1.l.google.com:19302"]}]

    if settings.TURN_SECRET and settings.TURN_HOST:
        # Time-limited HMAC credentials (TURN REST API convention) — valid 6h,
        # derived from the shared secret; nothing static leaks to clients.
        import base64
        import hashlib
        import hmac
        import time

        username = str(int(time.time()) + 6 * 3600)
        credential = base64.b64encode(
            hmac.new(settings.TURN_SECRET.encode(), username.encode(),
                     hashlib.sha1).digest()
        ).decode()
        ice.append({
            "urls": [
                f"turn:{settings.TURN_HOST}:3478?transport=udp",
                f"turn:{settings.TURN_HOST}:3478?transport=tcp",
            ],
            "username": username,
            "credential": credential,
        })
    elif settings.TURN_URL:
        entry: dict = {"urls": [settings.TURN_URL]}
        if settings.TURN_USERNAME:
            entry["username"] = settings.TURN_USERNAME
            entry["credential"] = settings.TURN_CREDENTIAL
        ice.append(entry)

    return {"iceServers": ice}
