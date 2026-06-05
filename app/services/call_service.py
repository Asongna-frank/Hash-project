# app/services/call_service.py
"""
Doctor → patient voice calls (WebRTC) — signaling layer.

Media never touches this server: the dashboard (browser) and the patient app
negotiate a direct WebRTC audio connection. We only do:
  1. Call session lifecycle (ringing → active → ended) in memory.
  2. Signal relay: SDP offers/answers and ICE candidates are forwarded
     between the hospital's /alerts/ws socket and the patient's /chat/ws
     socket (both already exist).
  3. Ring timeout (45s) → missed-call handling: both sides notified, a
     "missed call" message stored in the conversation, and a push sent so
     the patient sees it even if the app was closed.

Single-worker requirement: like the alert fan-out, the managers are
in-process (see ecosystem.config.js).
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.services.alert_service import manager as hospital_manager

logger = logging.getLogger(__name__)

RING_TIMEOUT_SECONDS = 45


# ── patient socket registry (mirror of the hospital one) ─────────────────────

class PatientConnectionManager:
    """Open patient chat sockets, keyed by patient_id (str)."""

    def __init__(self) -> None:
        self._connections: dict[str, set] = {}

    def register(self, patient_id: str, websocket) -> None:
        self._connections.setdefault(patient_id, set()).add(websocket)

    def unregister(self, patient_id: str, websocket) -> None:
        conns = self._connections.get(patient_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                del self._connections[patient_id]

    def is_online(self, patient_id: str) -> bool:
        return bool(self._connections.get(patient_id))

    async def send(self, patient_id: str, payload: dict) -> bool:
        """Send to every open socket of this patient. True if any delivery."""
        delivered = False
        text = json.dumps(payload)
        for ws in list(self._connections.get(patient_id, ())):
            try:
                await ws.send_text(text)
                delivered = True
            except Exception:  # noqa: BLE001
                self.unregister(patient_id, ws)
        return delivered


patient_manager = PatientConnectionManager()


# ── call sessions ─────────────────────────────────────────────────────────────

@dataclass
class CallSession:
    call_id: str
    hospital_id: str
    patient_id: str
    state: str = "ringing"  # ringing | active | ended
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    answered_at: Optional[datetime] = None


class CallManager:
    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}

    def create(self, hospital_id: str, patient_id: str) -> CallSession:
        # One live call per patient at a time.
        for s in self._sessions.values():
            if s.patient_id == patient_id and s.state in ("ringing", "active"):
                raise ValueError("Patient already has a call in progress")
        session = CallSession(call_id=f"call_{uuid.uuid4().hex[:12]}",
                              hospital_id=hospital_id, patient_id=patient_id)
        self._sessions[session.call_id] = session
        return session

    def get(self, call_id: str) -> Optional[CallSession]:
        return self._sessions.get(call_id)

    def end(self, call_id: str) -> Optional[CallSession]:
        session = self._sessions.pop(call_id, None)
        if session:
            session.state = "ended"
        return session

    def find_for_patient(self, patient_id: str) -> Optional[CallSession]:
        for s in self._sessions.values():
            if s.patient_id == patient_id and s.state in ("ringing", "active"):
                return s
        return None

    def find_for_hospital(self, hospital_id: str) -> list[CallSession]:
        return [s for s in self._sessions.values()
                if s.hospital_id == hospital_id and s.state in ("ringing", "active")]


calls = CallManager()


# ── history + push helpers (sync, called via threadpool) ─────────────────────

def _log_call_message(patient_id: str, text: str) -> None:
    """Store a call event in the conversation history (best-effort)."""
    from app.core.database import SessionLocal
    from app.services.message_store import save_outbound

    db = SessionLocal()
    try:
        db.add(save_outbound(patient_id, text, channel="app", message_type="chat"))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Call log failed | patient=%s", patient_id)
    finally:
        db.close()


def _push(patient_id: str, title: str, body: str) -> None:
    from app.services.push_service import push_service
    try:
        push_service.send_push(patient_id, title, body)
    except Exception:  # noqa: BLE001
        logger.exception("Call push failed | patient=%s", patient_id)


# ── lifecycle operations (async — called from routes/WS handlers) ────────────

async def notify_hospital(hospital_id: str, payload: dict) -> None:
    await hospital_manager.broadcast(hospital_id, payload)


async def ring_timeout_watch(call_id: str) -> None:
    """Background task: if still ringing after RING_TIMEOUT_SECONDS → missed."""
    await asyncio.sleep(RING_TIMEOUT_SECONDS)
    session = calls.get(call_id)
    if session is None or session.state != "ringing":
        return
    calls.end(call_id)
    logger.info("Call missed (ring timeout) | call=%s | patient=%s",
                call_id, session.patient_id)
    await patient_manager.send(session.patient_id,
                               {"type": "call_ended", "call_id": call_id, "reason": "missed"})
    await notify_hospital(session.hospital_id,
                          {"type": "call_ended", "call_id": call_id, "reason": "no_answer"})
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(_log_call_message, session.patient_id,
                            "📞 Missed voice call from your care team")
    await run_in_threadpool(_push, session.patient_id,
                            "Missed call", "Your care team tried to call you — open HASH to chat.")


async def handle_patient_frame(patient_id: str, data: dict) -> Optional[dict]:
    """
    Patient-side call actions (from /chat/ws). Returns an error frame for the
    patient, or None when handled.
    """
    action = data.get("action")
    call_id = data.get("call_id") or ""
    session = calls.get(call_id)
    if session is None or session.patient_id != patient_id:
        return {"type": "error", "detail": "Unknown call"}

    if action == "call_answer":
        if session.state != "ringing":
            return {"type": "error", "detail": "Call is not ringing"}
        if data.get("accept"):
            session.state = "active"
            session.answered_at = datetime.now(timezone.utc)
            await notify_hospital(session.hospital_id,
                                  {"type": "call_answered", "call_id": call_id})
        else:
            calls.end(call_id)
            await notify_hospital(session.hospital_id,
                                  {"type": "call_ended", "call_id": call_id, "reason": "declined"})
        return None

    if action == "call_signal":
        await notify_hospital(session.hospital_id,
                              {"type": "call_signal", "call_id": call_id,
                               "payload": data.get("payload")})
        return None

    if action == "call_end":
        await _end_call(session, by="patient")
        return None

    return {"type": "error", "detail": f"Unknown call action '{action}'"}


async def handle_hospital_frame(hospital_id: str, data: dict, websocket) -> None:
    """Hospital-side call actions (from /alerts/ws). Errors go back on `websocket`."""
    action = data.get("action")
    call_id = data.get("call_id") or ""
    session = calls.get(call_id)
    if session is None or session.hospital_id != hospital_id:
        await websocket.send_text(json.dumps({"type": "error", "detail": "Unknown call"}))
        return

    if action == "call_signal":
        await patient_manager.send(session.patient_id,
                                   {"type": "call_signal", "call_id": call_id,
                                    "payload": data.get("payload")})
        return

    if action == "call_end":
        await _end_call(session, by="hospital")
        return

    await websocket.send_text(json.dumps({"type": "error",
                                          "detail": f"Unknown call action '{action}'"}))


async def _end_call(session: CallSession, by: str) -> None:
    was_active = session.state == "active"
    calls.end(session.call_id)
    frame = {"type": "call_ended", "call_id": session.call_id, "reason": f"ended_by_{by}"}
    await patient_manager.send(session.patient_id, frame)
    await notify_hospital(session.hospital_id, frame)
    if was_active and session.answered_at:
        mins = max(round((datetime.now(timezone.utc) - session.answered_at).total_seconds() / 60), 1)
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(_log_call_message, session.patient_id,
                                f"📞 Voice call with your care team ({mins} min)")


async def end_calls_for_disconnected_patient(patient_id: str) -> None:
    """Patient socket dropped — tear down any live call so the doctor isn't left hanging."""
    session = calls.find_for_patient(patient_id)
    if session:
        calls.end(session.call_id)
        await notify_hospital(session.hospital_id,
                              {"type": "call_ended", "call_id": session.call_id,
                               "reason": "patient_disconnected"})
