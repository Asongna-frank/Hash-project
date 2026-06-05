# tests/test_m6_alerting.py
"""
Phase-1 tests: M6 Emergency & Alerting, the deterministic red-flag layer, and
the clinician Mark Loss / Live Birth flow (SRS activation hard rule).

DB and external providers (SMS, push, LLM) are mocked — these tests cover the
alert pipeline logic, red-flag matching, WS auth, and the activation rule.

Run:  pytest tests/test_m6_alerting.py -v
"""

import json
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.services import chat_core
from app.services.red_flags import is_crisis_signal, match_red_flags
from app.services.sms_service import SMSResult
from app.utils.auth import create_access_token

client = TestClient(app)


def hospital_token(hospital_id=None):
    return create_access_token({
        "sub": "+237650000001", "type": "hospital",
        "user_id": hospital_id or str(uuid.uuid4()),
    })


def patient_token(patient_id=None):
    return create_access_token({
        "sub": "+237679977660", "type": "patient",
        "user_id": patient_id or str(uuid.uuid4()),
    })


def make_patient(**over):
    """Complete fake patient — satisfies PatientResponse validation."""
    base = dict(
        id=uuid.uuid4(), hospital_id=uuid.uuid4(), name="Maria Nkeng",
        phone="+237679977660", age=29, risk_level="low", account_type="smartphone",
        status="active", language="en", lmp=date(2026, 1, 1), edd=date(2026, 10, 8),
        weeks_pregnant_at_signup=12, parity=1, preferred_support="none",
        previous_loss=False, previous_loss_count=0, previous_stillbirth=False,
        previous_caesarean=False, previous_preeclampsia=False,
        has_hypertension=False, has_diabetes=False, has_sickle_cell=False,
        has_hiv=False, has_severe_anaemia=False, multiple_pregnancy=False,
        late_anc_initiation=False, no_prior_anc=False, gravidity=None,
        blood_group=None, distance_close_to_hospital=None, rh_negative=False,
        consecutive_missed_checkins=0, missed_checkin_flag=False,
        pending_loss_confirmation=False, opt_out_status=None, paused_until=None,
        is_active=True, risk_level_set_at=None, risk_level_set_by=None,
        created_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── red-flag layer ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "I am bleeding heavily since morning",
    "my baby is not moving today",
    "I have a severe headache and blurred vision",
    "j'ai mal — severe pain in my stomach",   # english pivot phrase present
    "My water broke an hour ago",
    "I have high fever and smelly discharge",
])
def test_red_flags_match_danger_signs(msg):
    assert match_red_flags(msg), msg


@pytest.mark.parametrize("msg", [
    "I want to die",
    "sometimes I think of ways to hurt myself",
    "there is no reason to live anymore",
])
def test_red_flags_match_crisis_signals(msg):
    flags = match_red_flags(msg)
    assert flags and is_crisis_signal(flags), msg


@pytest.mark.parametrize("msg", [
    "I feel fine today, thank you",
    "what food is good for the baby?",
    "I had a small headache yesterday but it passed",
])
def test_red_flags_ignore_normal_messages(msg):
    assert match_red_flags(msg) == [], msg


def test_red_flag_forces_high_even_when_llm_says_low():
    """The deterministic layer must override the LLM classification (SRS M4)."""
    patient = make_patient()
    db = MagicMock()
    with patch.object(chat_core, "generate_reply", return_value=("ok, noted", "low")), \
         patch.object(chat_core, "detect_loss", return_value="NOT_A_LOSS"), \
         patch.object(chat_core, "save_inbound", return_value=MagicMock()), \
         patch.object(chat_core, "save_outbound", return_value=MagicMock()), \
         patch.object(chat_core, "_get_pregnancy", return_value=None), \
         patch.object(chat_core, "create_alert") as mock_alert:
        reply = chat_core.process_message(patient, "I am bleeding a lot", "app", db)
    assert reply.triage_level == "high"
    mock_alert.assert_called_once()
    assert "bleeding" in mock_alert.call_args.kwargs["reason"]


def test_normal_message_no_alert():
    patient = make_patient()
    db = MagicMock()
    with patch.object(chat_core, "generate_reply", return_value=("glad to hear", "low")), \
         patch.object(chat_core, "detect_loss", return_value="NOT_A_LOSS"), \
         patch.object(chat_core, "save_inbound", return_value=MagicMock()), \
         patch.object(chat_core, "save_outbound", return_value=MagicMock()), \
         patch.object(chat_core, "_get_pregnancy", return_value=None), \
         patch.object(chat_core, "create_alert") as mock_alert:
        reply = chat_core.process_message(patient, "I feel fine today", "app", db)
    assert reply.triage_level == "low"
    mock_alert.assert_not_called()


# ── SRS hard rule: chat never activates the post-loss track ───────────────────

def test_chat_loss_report_flags_but_never_activates_post_loss():
    patient = make_patient()
    db = MagicMock()
    with patch.object(chat_core, "generate_reply", return_value=("", "low")), \
         patch.object(chat_core, "detect_loss", return_value="CONFIRMED"), \
         patch.object(chat_core, "save_inbound", return_value=MagicMock()), \
         patch.object(chat_core, "save_outbound", return_value=MagicMock()), \
         patch.object(chat_core, "_get_pregnancy", return_value=None), \
         patch.object(chat_core.content_store, "get_content",
                      return_value="I'm so deeply sorry for your loss."), \
         patch.object(chat_core, "create_alert") as mock_alert:
        reply = chat_core.process_message(patient, "I lost my baby yesterday", "app", db)

    # Empathetic crisis reply goes out immediately...
    assert reply.is_crisis is True
    assert reply.loss_detected is True
    assert reply.triage_level == "high"
    # ...the hospital is alerted for review...
    mock_alert.assert_called_once()
    assert "review" in mock_alert.call_args.kwargs["reason"].lower()
    # ...but the patient state is NOT switched (clinician-only activation).
    assert patient.status == "active"
    assert patient.risk_level == "low"


def test_post_loss_crisis_signal_raises_post_loss_crisis_alert():
    patient = make_patient(status="post_loss")
    db = MagicMock()
    with patch.object(chat_core, "generate_reply", return_value=("I hear you", "low")), \
         patch.object(chat_core, "save_inbound", return_value=MagicMock()), \
         patch.object(chat_core, "save_outbound", return_value=MagicMock()), \
         patch.object(chat_core, "_get_pregnancy", return_value=None), \
         patch.object(chat_core, "create_alert") as mock_alert:
        reply = chat_core.process_message(patient, "I want to die", "app", db)
    assert reply.triage_level == "high"
    assert mock_alert.call_args.kwargs["source"] == "post_loss_crisis"
    assert "self-harm" in mock_alert.call_args.kwargs["reason"].lower()


# ── alert service fan-out ─────────────────────────────────────────────────────

def test_create_alert_persists_broadcasts_and_smses():
    from app.services import alert_service

    patient = make_patient()
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    hospital = SimpleNamespace(id=patient.hospital_id, phone="+237650000001")
    db.query.return_value.filter.return_value.first.return_value = hospital

    with patch.object(alert_service.manager, "broadcast_threadsafe") as mock_bc, \
         patch.object(alert_service, "sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=True)
        alert = alert_service.create_alert(
            db, patient=patient, source="message_triage",
            reason="Red-flag message", triage_level="high",
        )

    db.add.assert_called()           # persisted
    db.commit.assert_called()        # committed immediately
    mock_bc.assert_called_once()     # real-time dashboard push
    payload = mock_bc.call_args[0][1]
    assert payload["type"] == "alert"
    assert payload["alert"]["source"] == "message_triage"
    assert payload["alert"]["patient"]["name"] == "Maria Nkeng"
    mock_sms.send_sms.assert_called_once()           # parallel hospital SMS
    assert "HASH ALERT" in mock_sms.send_sms.call_args[0][1]


def test_create_alert_survives_sms_failure():
    from app.services import alert_service

    patient = make_patient()
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    db.query.return_value.filter.return_value.first.return_value = None  # no hospital row

    with patch.object(alert_service.manager, "broadcast_threadsafe"), \
         patch.object(alert_service, "sms_service") as mock_sms:
        mock_sms.send_sms.side_effect = RuntimeError("provider down")
        alert = alert_service.create_alert(
            db, patient=patient, source="emergency_button", reason="test",
        )
    assert alert is not None  # alert still created despite SMS failure


# ── emergency button endpoint ─────────────────────────────────────────────────

def test_emergency_button_creates_high_alert_with_gps():
    import app.routers.alerts as alerts_router

    pid = str(uuid.uuid4())
    patient = make_patient(id=pid)
    fake_alert = SimpleNamespace(
        id=uuid.uuid4(), patient_id=patient.id, hospital_id=patient.hospital_id,
        source="emergency_button", triage_level="high",
        reason='EMERGENCY BUTTON pressed — "help"', context="", gps_lat=4.15,
        gps_lng=9.24, status="new",
        created_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc), acknowledged_at=None,
        resolved_at=None,
    )

    with patch.object(alerts_router, "create_alert", return_value=fake_alert) as mock_ca, \
         patch.object(alerts_router, "write_audit"), \
         patch("app.routers.alerts.get_db") as _:
        app.dependency_overrides = {}
        from app.core.database import get_db as real_get_db
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = patient
        app.dependency_overrides[real_get_db] = lambda: db
        try:
            resp = client.post(
                "/alerts/emergency",
                headers={"Authorization": f"Bearer {patient_token(pid)}"},
                json={"gps_lat": 4.15, "gps_lng": 9.24, "note": "help"},
            )
        finally:
            app.dependency_overrides = {}

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "emergency_button"
    assert body["gps_lat"] == 4.15
    assert mock_ca.call_args.kwargs["gps_lat"] == 4.15
    assert "EMERGENCY BUTTON" in mock_ca.call_args.kwargs["reason"]


def test_emergency_button_rejects_hospital_token():
    resp = client.post(
        "/alerts/emergency",
        headers={"Authorization": f"Bearer {hospital_token()}"},
        json={},
    )
    assert resp.status_code == 403


def test_list_alerts_requires_hospital():
    resp = client.get(
        "/alerts", headers={"Authorization": f"Bearer {patient_token()}"}
    )
    assert resp.status_code == 403


# ── hospital dashboard WebSocket ──────────────────────────────────────────────

def _expect_ws_close(url, expected_code):
    with client.websocket_connect(url) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == expected_code


def test_alerts_ws_missing_token_closes_4401():
    _expect_ws_close("/alerts/ws", 4401)


def test_alerts_ws_patient_token_closes_4403():
    _expect_ws_close(f"/alerts/ws?token={patient_token()}", 4403)


def test_alerts_ws_connect_snapshot_and_live_push():
    import app.routers.alerts as alerts_router
    from app.services.alert_service import manager

    hid = str(uuid.uuid4())
    with patch.object(alerts_router, "SessionLocal") as mock_sl:
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = []
        mock_sl.return_value.query.return_value = q
        with client.websocket_connect(f"/alerts/ws?token={hospital_token(hid)}") as ws:
            assert json.loads(ws.receive_text()) == {"type": "connected"}
            snap = json.loads(ws.receive_text())
            assert snap["type"] == "alerts_snapshot"
            assert snap["items"] == []

            # The manager registered this socket → a broadcast reaches it live.
            manager.broadcast_threadsafe(hid, {"type": "alert", "alert": {"id": "x"}})
            frame = json.loads(ws.receive_text())
            assert frame == {"type": "alert", "alert": {"id": "x"}}

            ws.send_text(json.dumps({"action": "ping"}))
            assert json.loads(ws.receive_text()) == {"type": "pong"}


# ── clinician outcome endpoint ────────────────────────────────────────────────

def test_mark_loss_activates_post_loss_track():
    import app.routers.hospital_patients as hp

    hid = str(uuid.uuid4())
    patient = make_patient(hospital_id=hid, status="active")
    from app.core.database import get_db as real_get_db

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = patient
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    def fake_activate(db_, p, preg, **kw):
        p.status = "post_loss"
        return "opener"

    with patch.object(hp, "activate_post_loss", side_effect=fake_activate) as mock_act, \
         patch.object(hp, "write_audit"):
        app.dependency_overrides[real_get_db] = lambda: db
        try:
            resp = client.post(
                f"/hospital/patients/{patient.id}/outcome",
                headers={"Authorization": f"Bearer {hospital_token(hid)}"},
                json={"outcome": "loss", "ga_at_loss": 18},
            )
        finally:
            app.dependency_overrides = {}

    assert resp.status_code == 200, resp.text
    mock_act.assert_called_once()
    assert mock_act.call_args.kwargs["ga_at_loss"] == 18
    assert patient.status == "post_loss"


def test_mark_loss_other_hospitals_patient_404():
    patient = make_patient(hospital_id=str(uuid.uuid4()))  # owned elsewhere
    from app.core.database import get_db as real_get_db

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = patient
    app.dependency_overrides[real_get_db] = lambda: db
    try:
        resp = client.post(
            f"/hospital/patients/{patient.id}/outcome",
            headers={"Authorization": f"Bearer {hospital_token()}"},  # different hospital
            json={"outcome": "loss"},
        )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 404


def test_mark_loss_twice_409():
    hid = str(uuid.uuid4())
    patient = make_patient(hospital_id=hid, status="post_loss")
    from app.core.database import get_db as real_get_db

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = patient
    app.dependency_overrides[real_get_db] = lambda: db
    try:
        resp = client.post(
            f"/hospital/patients/{patient.id}/outcome",
            headers={"Authorization": f"Bearer {hospital_token(hid)}"},
            json={"outcome": "loss"},
        )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 409


def test_mark_live_birth_sets_delivered():
    import app.routers.hospital_patients as hp

    hid = str(uuid.uuid4())
    patient = make_patient(hospital_id=hid, status="active")
    from app.core.database import get_db as real_get_db

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = patient
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    def fake_birth(db_, p, preg):
        p.status = "delivered"

    with patch.object(hp, "mark_live_birth", side_effect=fake_birth), \
         patch.object(hp, "write_audit"):
        app.dependency_overrides[real_get_db] = lambda: db
        try:
            resp = client.post(
                f"/hospital/patients/{patient.id}/outcome",
                headers={"Authorization": f"Bearer {hospital_token(hid)}"},
                json={"outcome": "live_birth"},
            )
        finally:
            app.dependency_overrides = {}
    assert resp.status_code == 200
    assert patient.status == "delivered"
