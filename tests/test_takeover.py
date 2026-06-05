# tests/test_takeover.py
"""
Clinician chat intervention + takeover lifecycle:
join (bot silent, patient signalled) -> doctor messages -> patient messages
relayed to dashboard with red flags -> leave (bot resumes).

Run:  pytest tests/test_takeover.py -v
"""

import json
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app.routers.alerts as alerts_router
import app.routers.chat as chat_router
from app.core.database import get_db
from app.main import app
from app.services.chat_core import ChatReply
from app.services.takeover import takeovers
from app.utils.auth import create_access_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean():
    takeovers._active.clear()
    yield
    takeovers._active.clear()


def ids_and_tokens():
    hid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    return (hid, pid,
            create_access_token({"sub": "+h", "type": "hospital", "user_id": hid}),
            create_access_token({"sub": "+p", "type": "patient", "user_id": pid}))


def make_patient(pid, hid, **over):
    base = dict(id=pid, hospital_id=hid, name="Maria", language="en",
                account_type="smartphone", is_active=True, status="active",
                lmp=date(2026, 1, 1))
    base.update(over)
    return SimpleNamespace(**base)


def scoped_db(patient, hospital=None):
    db = MagicMock()
    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.offset.return_value = q
        q.count.return_value = 0
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


def empty_session(mock_sl):
    q = MagicMock(); q.filter.return_value = q; q.order_by.return_value = q
    q.limit.return_value = q; q.all.return_value = []; q.first.return_value = None
    mock_sl.return_value.query.return_value = q


def test_full_takeover_cycle():
    hid, pid, htok, ptok = ids_and_tokens()
    patient = make_patient(pid, hid)
    hospital = SimpleNamespace(id=hid, name="Buea Regional Hospital")

    with patch.object(chat_router, "SessionLocal") as sl_chat, \
         patch.object(alerts_router, "SessionLocal") as sl_alerts:
        empty_session(sl_chat)
        empty_session(sl_alerts)
        app.dependency_overrides[get_db] = lambda: scoped_db(patient, hospital)
        try:
            with client.websocket_connect(f"/alerts/ws?token={htok}") as hws, \
                 client.websocket_connect(f"/chat/ws?token={ptok}") as pws:
                hws.receive_text(); hws.receive_text()
                pws.receive_text(); pws.receive_text()

                # 1) doctor joins
                r = client.post(f"/hospital/patients/{pid}/takeover",
                                headers={"Authorization": f"Bearer {htok}"},
                                json={"author_name": "Dr Elvira"})
                assert r.status_code == 201, r.text
                assert r.json()["patient_online"] is True
                joined = json.loads(pws.receive_text())
                assert joined == {"type": "clinician_joined",
                                  "author_name": "Dr Elvira",
                                  "hospital_name": "Buea Regional Hospital"}

                # second join attempt -> 409
                assert client.post(f"/hospital/patients/{pid}/takeover",
                                   headers={"Authorization": f"Bearer {htok}"},
                                   json={}).status_code == 409

                # 2) patient message -> NO bot reply, relayed to dashboard
                q2 = MagicMock(); q2.filter.return_value = q2
                q2.order_by.return_value = q2; q2.limit.return_value = q2
                q2.all.return_value = []; q2.first.return_value = patient
                sl_chat.return_value.query.return_value = q2
                with patch.object(chat_router, "save_inbound",
                                  return_value=SimpleNamespace(id=uuid.uuid4(), created_at=None)), \
                     patch.object(chat_router, "process_message") as brain, \
                     patch.object(chat_router, "create_alert") as alert:
                    pws.send_text(json.dumps({"message": "I am bleeding a lot",
                                              "client_msg_id": "t1"}))
                    assert json.loads(pws.receive_text())["type"] == "ack"
                    relayed = json.loads(pws.receive_text())
                    assert relayed["type"] == "message_relayed"
                    assert relayed["triage_level"] == "high"     # red flags still on
                    brain.assert_not_called()                    # bot stayed silent
                    alert.assert_called_once()                   # danger still alerts

                    dash = json.loads(hws.receive_text())
                    assert dash["type"] == "patient_message"
                    assert dash["content"] == "I am bleeding a lot"
                    assert dash["triage_level"] == "high"

                # 3) doctor leaves -> patient signalled, bot resumes
                r = client.delete(f"/hospital/patients/{pid}/takeover",
                                  headers={"Authorization": f"Bearer {htok}"})
                assert r.status_code == 200
                assert json.loads(pws.receive_text()) == {"type": "clinician_left"}

                fake_reply = ChatReply(text="I'm here with you.", channel="app",
                                       triage_level="low")
                with patch.object(chat_router, "process_message",
                                  return_value=fake_reply) as brain:
                    # patient lookup inside _process_chat uses SessionLocal
                    q = MagicMock(); q.filter.return_value = q
                    q.first.return_value = patient
                    q.order_by.return_value = q; q.limit.return_value = q
                    q.all.return_value = []
                    sl_chat.return_value.query.return_value = q
                    pws.send_text(json.dumps({"message": "thank you doctor"}))
                    assert json.loads(pws.receive_text())["type"] == "ack"
                    assert json.loads(pws.receive_text())["type"] == "typing"  # bot is back
                    brain.assert_called_once()
        finally:
            app.dependency_overrides = {}


def test_takeover_resignalled_on_reconnect():
    hid, pid, htok, ptok = ids_and_tokens()
    takeovers.start(pid, hid, "Dr Test", "Buea Regional Hospital")
    with patch.object(chat_router, "SessionLocal") as sl_chat:
        empty_session(sl_chat)
        with client.websocket_connect(f"/chat/ws?token={ptok}") as pws:
            first = json.loads(pws.receive_text())
            assert first["type"] == "clinician_joined"
            assert first["author_name"] == "Dr Test"


def test_takeover_end_idempotent_and_scoped():
    hid, pid, htok, _ = ids_and_tokens()
    patient = make_patient(pid, hid)
    app.dependency_overrides[get_db] = lambda: scoped_db(patient)
    try:
        r = client.delete(f"/hospital/patients/{pid}/takeover",
                          headers={"Authorization": f"Bearer {htok}"})
        assert r.status_code == 200          # idempotent: nothing active
        # other hospital's patient -> 404
        other = make_patient(pid, str(uuid.uuid4()))
        app.dependency_overrides[get_db] = lambda: scoped_db(other)
        r = client.post(f"/hospital/patients/{pid}/takeover",
                        headers={"Authorization": f"Bearer {htok}"}, json={})
        assert r.status_code == 404
    finally:
        app.dependency_overrides = {}
