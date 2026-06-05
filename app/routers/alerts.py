# app/routers/alerts.py
"""
M6 Emergency & Alerting — patient emergency button, hospital alert queue, and
the real-time hospital dashboard WebSocket.

Transports only — alert creation/fan-out logic lives in alert_service.
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.models.alert import Alert
from app.models.patient import Patient
from app.schemas.alert import AlertResponse, AlertStatusUpdate, EmergencyRequest
from app.services.alert_service import alert_to_dict, create_alert, manager
from app.services.audit import write_audit
from app.utils.access import require_hospital, require_patient

router = APIRouter()
logger = logging.getLogger(__name__)

WS_POLICY_VIOLATION = 4401  # missing/invalid/expired token
WS_FORBIDDEN = 4403         # valid token but not a hospital token


def _alert_with_patient(alert: Alert, db: Session) -> dict:
    patient = db.query(Patient).filter(Patient.id == alert.patient_id).first()
    return alert_to_dict(alert, patient)


# ── patient side: emergency button ───────────────────────────────────────────

@router.post(
    "/emergency",
    response_model=AlertResponse,
    status_code=201,
    summary="Emergency button (patient)",
    description=(
        "Patient-only. The app's emergency button: raises an immediate High "
        "alert to the patient's hospital with her GPS position (if granted) and "
        "her recent conversation context. The alert is pushed to the hospital "
        "dashboard in real time and an SMS is sent to the hospital in parallel. "
        "GPS is optional — without it the dashboard uses the registered address."
    ),
)
def emergency_button(
    body: EmergencyRequest,
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    reason = "EMERGENCY BUTTON pressed"
    if body.note and body.note.strip():
        reason += f' — "{body.note.strip()[:200]}"'

    alert = create_alert(
        db, patient=patient, source="emergency_button", reason=reason,
        triage_level="high", gps_lat=body.gps_lat, gps_lng=body.gps_lng,
    )

    write_audit(
        db, actor_type="patient", actor_id=patient.id,
        action="alert.emergency_button", target_type="alert", target_id=alert.id,
        details={"gps": body.gps_lat is not None},
    )
    db.commit()

    return _alert_with_patient(alert, db)


# ── hospital side: alert queue ────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[AlertResponse],
    summary="List the hospital's alerts",
    description=(
        "Hospital-only. Newest first. Filter with ?status=new|ack|resolved. "
        "Each alert embeds a patient summary (name, phone, risk level, track) "
        "so the dashboard pane renders without extra lookups."
    ),
)
def list_alerts(
    status: str | None = Query(default=None, pattern="^(new|ack|resolved)$"),
    skip: int = 0,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
    hospital_id: str = Depends(require_hospital),
):
    q = db.query(Alert).filter(Alert.hospital_id == hospital_id)
    if status:
        q = q.filter(Alert.status == status)
    alerts = q.order_by(Alert.created_at.desc()).offset(skip).limit(limit).all()
    return [_alert_with_patient(a, db) for a in alerts]


@router.patch(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Acknowledge or resolve an alert",
    description=(
        "Hospital-only, own alerts only (others → 404). Body {\"status\": \"ack\"} "
        "stamps acknowledged_at; {\"status\": \"resolved\"} stamps resolved_at. "
        "The change is audited and broadcast to all connected dashboard sockets "
        "so every open dashboard updates in real time."
    ),
)
def update_alert_status(
    alert_id: UUID,
    body: AlertStatusUpdate,
    db: Session = Depends(get_db),
    hospital_id: str = Depends(require_hospital),
):
    if body.status not in ("ack", "resolved"):
        raise HTTPException(status_code=422, detail="status must be 'ack' or 'resolved'")

    alert = (
        db.query(Alert)
        .filter(Alert.id == alert_id, Alert.hospital_id == hospital_id)
        .first()
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    now = datetime.now(timezone.utc)
    alert.status = body.status
    if body.status == "ack" and alert.acknowledged_at is None:
        alert.acknowledged_at = now
    if body.status == "resolved":
        if alert.acknowledged_at is None:
            alert.acknowledged_at = now
        alert.resolved_at = now

    write_audit(
        db, actor_type="hospital", actor_id=hospital_id,
        action=f"alert.{body.status}", target_type="alert", target_id=alert.id,
        details={"source": alert.source},
    )
    db.commit()
    db.refresh(alert)

    payload = _alert_with_patient(alert, db)
    manager.broadcast_threadsafe(hospital_id, {"type": "alert_updated", "alert": payload})
    return payload


# ── hospital dashboard WebSocket ──────────────────────────────────────────────

def _ws_authenticate_hospital(token: str | None) -> tuple[str | None, int, str]:
    """Validate a hospital JWT for the dashboard WebSocket handshake."""
    if not token:
        return None, WS_POLICY_VIOLATION, "Missing token"
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None, WS_POLICY_VIOLATION, "Invalid or expired token"
    if payload.get("type") != "hospital":
        return None, WS_FORBIDDEN, "Hospital access required"
    return payload["user_id"], 0, ""


def _fetch_open_alerts(hospital_id: str) -> dict:
    """Snapshot of unresolved alerts, pushed on connect."""
    db: Session = SessionLocal()
    try:
        rows = (
            db.query(Alert)
            .filter(Alert.hospital_id == hospital_id, Alert.status != "resolved")
            .order_by(Alert.created_at.desc())
            .limit(100)
            .all()
        )
        return {
            "type": "alerts_snapshot",
            "items": [_alert_with_patient(a, db) for a in rows],
        }
    finally:
        db.close()


@router.websocket("/ws")
async def alerts_websocket(
    websocket: WebSocket,
    token: str | None = Query(default=None),
):
    """
    Real-time alert feed: WS /alerts/ws?token=<hospital JWT>.

    On connect the server pushes {"type": "connected"} then
    {"type": "alerts_snapshot", "items": [...]} (all unresolved alerts).
    New alerts arrive as {"type": "alert", "alert": {...}} the moment they are
    raised (≤30s SRS requirement — in practice instant). Status changes arrive
    as {"type": "alert_updated", "alert": {...}}.

    Client frames: {"action": "ping"} → {"type": "pong"}.
    Close codes: 4401 bad/missing token, 4403 not a hospital token.
    """
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

    hospital_id, close_code, close_reason = _ws_authenticate_hospital(token)

    await websocket.accept()
    if hospital_id is None:
        await websocket.close(code=close_code, reason=close_reason)
        return

    manager.register(hospital_id, websocket)
    logger.info("Dashboard WS connected | hospital=%s", hospital_id)

    try:
        await websocket.send_text(json.dumps({"type": "connected"}))
        snapshot = await run_in_threadpool(_fetch_open_alerts, hospital_id)
        await websocket.send_text(json.dumps(snapshot))

        while True:
            raw_frame = await websocket.receive_text()
            try:
                data = json.loads(raw_frame)
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict) and data.get("action") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            # All mutations (ack/resolve) go through the REST PATCH so they are
            # validated + audited in one place; the result is broadcast back here.

    except WebSocketDisconnect:
        logger.info("Dashboard WS disconnected | hospital=%s", hospital_id)
    except Exception:  # noqa: BLE001
        logger.exception("Dashboard WS error | hospital=%s", hospital_id)
    finally:
        manager.unregister(hospital_id, websocket)
