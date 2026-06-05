# app/services/alert_service.py
"""
M6 Emergency & Alerting — the single place an alert is born and fanned out.

create_alert() is the only entry point. It:
  1. Persists the Alert row (committed immediately — an alert must never be
     lost to a later rollback).
  2. Pushes it in real time to every hospital-dashboard WebSocket connected
     for that hospital (≤ 30s SRS requirement — in practice instant).
  3. Sends an SMS to the hospital's registered phone in parallel (best-effort:
     SMS failure never blocks the alert).

The in-process ConnectionManager requires the API to run as a SINGLE uvicorn
worker (see ecosystem.config.js) — which is also required for APScheduler not
to double-fire. If we ever scale to multiple workers, swap the manager for a
Redis pub/sub bridge; create_alert's contract stays the same.
"""

import asyncio
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.hospital import Hospital
from app.models.message import Message
from app.models.patient import Patient
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)

CONTEXT_MESSAGE_COUNT = 5  # recent messages included in the alert payload


# ── real-time fan-out ─────────────────────────────────────────────────────────

class HospitalConnectionManager:
    """Tracks open hospital-dashboard WebSockets, keyed by hospital_id (str)."""

    def __init__(self) -> None:
        self._connections: dict[str, set] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def register(self, hospital_id: str, websocket) -> None:
        # Capture the running loop so sync code (chat brain in the threadpool,
        # APScheduler jobs) can schedule broadcasts onto it.
        self._loop = asyncio.get_running_loop()
        self._connections.setdefault(hospital_id, set()).add(websocket)
        logger.info("Dashboard WS registered | hospital=%s | open=%d",
                    hospital_id, len(self._connections[hospital_id]))

    def unregister(self, hospital_id: str, websocket) -> None:
        conns = self._connections.get(hospital_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                del self._connections[hospital_id]

    async def broadcast(self, hospital_id: str, payload: dict) -> None:
        """Send payload to every socket of this hospital; drop dead sockets."""
        text = json.dumps(payload)
        for ws in list(self._connections.get(hospital_id, ())):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001 — a dead socket must not stop the others
                self.unregister(hospital_id, ws)

    def broadcast_threadsafe(self, hospital_id: str, payload: dict) -> None:
        """Schedule a broadcast from synchronous code (threadpool / scheduler)."""
        if self._loop is None or self._loop.is_closed():
            return  # no dashboard has ever connected in this process
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast(hospital_id, payload), self._loop
            )
        except RuntimeError:
            logger.warning("Dashboard broadcast skipped — event loop unavailable")


manager = HospitalConnectionManager()


# ── helpers ───────────────────────────────────────────────────────────────────

def _recent_context(db: Session, patient_id) -> str:
    """Last few messages as a compact excerpt (SRS 2.5: alert includes context)."""
    rows = (
        db.query(Message)
        .filter(Message.patient_id == patient_id)
        .order_by(Message.created_at.desc())
        .limit(CONTEXT_MESSAGE_COUNT)
        .all()
    )
    lines = []
    for m in reversed(rows):
        who = "Patient" if m.direction == "in" else "Bot"
        lines.append(f"{who}: {m.content}")
    return "\n".join(lines)


def alert_to_dict(alert: Alert, patient: Patient | None = None) -> dict:
    d = {
        "id": str(alert.id),
        "patient_id": str(alert.patient_id),
        "hospital_id": str(alert.hospital_id),
        "source": alert.source,
        "triage_level": alert.triage_level,
        "reason": alert.reason,
        "context": alert.context,
        "gps_lat": alert.gps_lat,
        "gps_lng": alert.gps_lng,
        "status": alert.status,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }
    if patient is not None:
        d["patient"] = {
            "name": patient.name,
            "phone": patient.phone,
            "age": patient.age,
            "risk_level": patient.risk_level,
            "account_type": patient.account_type,
            "status": patient.status,
        }
    return d


# ── public entry point ────────────────────────────────────────────────────────

def create_alert(
    db: Session,
    *,
    patient: Patient,
    source: str,
    reason: str,
    triage_level: str = "high",
    gps_lat: float | None = None,
    gps_lng: float | None = None,
    context: str | None = None,
) -> Alert:
    """
    Create, persist, and fan out one alert. Safe to call from sync code
    (chat brain, scheduler jobs) — the WebSocket push is scheduled threadsafe
    and the SMS is best-effort.
    """
    alert = Alert(
        patient_id=patient.id,
        hospital_id=patient.hospital_id,
        source=source,
        triage_level=triage_level,
        reason=reason,
        context=context if context is not None else _recent_context(db, patient.id),
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        status="new",
    )
    db.add(alert)
    db.commit()  # an alert must exist even if the caller later rolls back
    db.refresh(alert)

    logger.warning(
        "HOSPITAL ALERT | hospital=%s | patient=%s | source=%s | %s",
        patient.hospital_id, patient.id, source, reason,
    )

    # 1) Real-time dashboard push
    manager.broadcast_threadsafe(
        str(patient.hospital_id),
        {"type": "alert", "alert": alert_to_dict(alert, patient)},
    )

    # 2) Parallel SMS to the hospital's registered phone — best-effort
    try:
        hospital = db.query(Hospital).filter(Hospital.id == patient.hospital_id).first()
        if hospital and hospital.phone:
            sms = (
                f"HASH ALERT ({(triage_level or 'high').upper()}): "
                f"{patient.name} — {reason}. Open the dashboard now."
            )
            result = sms_service.send_sms(hospital.phone, sms)
            if not result.ok:
                logger.error("Alert SMS failed | hospital=%s | %s", hospital.id, result.error)
    except Exception:  # noqa: BLE001 — SMS must never block an alert
        logger.exception("Alert SMS fan-out error | alert=%s", alert.id)

    return alert
