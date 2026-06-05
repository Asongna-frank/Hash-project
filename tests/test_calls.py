# tests/test_calls.py
"""
Doctor → patient voice-call signaling tests: full protocol over both
WebSockets (hospital /alerts/ws + patient /chat/ws) with the REST start.

Push + DB are mocked; the in-memory managers and relay logic are real.

Run:  pytest tests/test_calls.py -v
"""

import json
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app.routers.alerts as alerts_router
import app.routers.calls as calls_router
import app.routers.chat as chat_router
from app.core.database import get_db
from app.main import app
from app.services.call_service import calls
from app.utils.auth import create_access_token

client = TestClient(app)


def tokens():
    hid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    htok = create_access_token({"sub": "+2376h", "type": "hospital", "user_id": hid})
    ptok = create_access_token({"sub": "+2376p", "type": "patient", "user_id": pid})
    return hid, pid, htok, ptok


def fake_db(patient, hospital=None):
    db = MagicMock()

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = []
        if model.__name__ == "Patient":
            q.first.return_value = patient
        elif model.__name__ == "Hospital":
            q.first.return_value = hospital
        else:
            q.first.return_value = None
        return q

    db.query.side_effect = query
    return db


def open_patient_ws(ws):
    assert json.loads(ws.receive_text())["type"] == "connected"
    assert json.loads(ws.receive_text())["type"] == "unread_notifications"


def open_hospital_ws(ws):
    assert json.loads(ws.receive_text())["type"] == "connected"
    assert json.loads(ws.receive_text())["type"] == "alerts_snapshot"


@pytest.fixture(autouse=True)
def clean_sessions():
    calls._sessions.clear()
    yield
    calls._sessions.clear()


def test_full_call_flow():
    hid, pid, htok, ptok = tokens()
    patient = SimpleNamespace(id=pid, hospital_id=hid, name="Maria",
                              account_type="smartphone", is_active=True,
                              lmp=date(2026, 1, 1), language="en", status="active")
    hospital = SimpleNamespace(id=hid, name="Buea Regional Hospital")

    with patch.object(chat_router, "SessionLocal") as mock_sl, \
         patch.object(alerts_router, "SessionLocal") as mock_sl2, \
         patch.object(calls_router, "_push") as mock_push:
        # connect-push queries return empty lists
        q = MagicMock(); q.filter.return_value = q; q.order_by.return_value = q
        q.limit.return_value = q; q.all.return_value = []; q.first.return_value = None
        mock_sl.return_value.query.return_value = q
        mock_sl2.return_value.query.return_value = q

        app.dependency_overrides[get_db] = lambda: fake_db(patient, hospital)
        try:
            with client.websocket_connect(f"/alerts/ws?token={htok}") as hws, \
                 client.websocket_connect(f"/chat/ws?token={ptok}") as pws:
                open_hospital_ws(hws)
                open_patient_ws(pws)

                # 1) doctor starts the call
                r = client.post("/calls", json={"patient_id": pid},
                                headers={"Authorization": f"Bearer {htok}"})
                assert r.status_code == 201, r.text
                body = r.json()
                call_id = body["call_id"]
                assert body["patient_online"] is True
                mock_push.assert_called_once()  # push fired too

                # 2) patient app rings
                ring = json.loads(pws.receive_text())
                assert ring == {"type": "incoming_call", "call_id": call_id,
                                "hospital_name": "Buea Regional Hospital"}

                # 3) patient accepts → doctor notified
                pws.send_text(json.dumps({"action": "call_answer",
                                          "call_id": call_id, "accept": True}))
                assert json.loads(hws.receive_text()) == {
                    "type": "call_answered", "call_id": call_id}

                # 4) SDP/ICE relay both directions
                hws.send_text(json.dumps({"action": "call_signal", "call_id": call_id,
                                          "payload": {"sdp": "offer-xyz"}}))
                frame = json.loads(pws.receive_text())
                assert frame["type"] == "call_signal"
                assert frame["payload"] == {"sdp": "offer-xyz"}

                pws.send_text(json.dumps({"action": "call_signal", "call_id": call_id,
                                          "payload": {"sdp": "answer-abc"}}))
                frame = json.loads(hws.receive_text())
                assert frame["payload"] == {"sdp": "answer-abc"}

                # 5) doctor hangs up → both sides get call_ended
                with patch("app.services.call_service._log_call_message") as log:
                    hws.send_text(json.dumps({"action": "call_end", "call_id": call_id}))
                    assert json.loads(pws.receive_text())["type"] == "call_ended"
                    assert json.loads(hws.receive_text())["type"] == "call_ended"
        finally:
            app.dependency_overrides = {}


def test_call_declined():
    hid, pid, htok, ptok = tokens()
    patient = SimpleNamespace(id=pid, hospital_id=hid, name="Maria",
                              account_type="smartphone", is_active=True,
                              lmp=date(2026, 1, 1), language="en", status="active")
    with patch.object(chat_router, "SessionLocal") as mock_sl, \
         patch.object(alerts_router, "SessionLocal") as mock_sl2, \
         patch.object(calls_router, "_push"):
        q = MagicMock(); q.filter.return_value = q; q.order_by.return_value = q
        q.limit.return_value = q; q.all.return_value = []; q.first.return_value = None
        mock_sl.return_value.query.return_value = q
        mock_sl2.return_value.query.return_value = q
        app.dependency_overrides[get_db] = lambda: fake_db(patient, None)
        try:
            with client.websocket_connect(f"/alerts/ws?token={htok}") as hws, \
                 client.websocket_connect(f"/chat/ws?token={ptok}") as pws:
                open_hospital_ws(hws)
                open_patient_ws(pws)
                r = client.post("/calls", json={"patient_id": pid},
                                headers={"Authorization": f"Bearer {htok}"})
                call_id = r.json()["call_id"]
                pws.receive_text()  # incoming_call
                pws.send_text(json.dumps({"action": "call_answer",
                                          "call_id": call_id, "accept": False}))
                ended = json.loads(hws.receive_text())
                assert ended == {"type": "call_ended", "call_id": call_id,
                                 "reason": "declined"}
                assert calls.get(call_id) is None  # session cleaned up
        finally:
            app.dependency_overrides = {}


def test_call_offline_patient_and_choronko():
    hid, pid, htok, _ = tokens()
    patient = SimpleNamespace(id=pid, hospital_id=hid, name="Maria",
                              account_type="smartphone", is_active=True,
                              lmp=date(2026, 1, 1), language="en", status="active")
    with patch.object(calls_router, "_push") as mock_push:
        app.dependency_overrides[get_db] = lambda: fake_db(patient, None)
        try:
            r = client.post("/calls", json={"patient_id": pid},
                            headers={"Authorization": f"Bearer {htok}"})
            assert r.status_code == 201
            assert r.json()["patient_online"] is False  # no socket open
            mock_push.assert_called_once()              # push still wakes the app

            # second call while one is ringing → 409
            r2 = client.post("/calls", json={"patient_id": pid},
                             headers={"Authorization": f"Bearer {htok}"})
            assert r2.status_code == 409

            # choronko → 422 with guidance
            patient.account_type = "choronko"
            calls._sessions.clear()
            r3 = client.post("/calls", json={"patient_id": pid},
                             headers={"Authorization": f"Bearer {htok}"})
            assert r3.status_code == 422
        finally:
            app.dependency_overrides = {}


def test_ice_config_requires_auth():
    assert client.get("/calls/ice-config").status_code in (401, 403)
    _, _, htok, _ = tokens()
    r = client.get("/calls/ice-config", headers={"Authorization": f"Bearer {htok}"})
    assert r.status_code == 200
    assert any("stun:" in u for s in r.json()["iceServers"] for u in s["urls"])
