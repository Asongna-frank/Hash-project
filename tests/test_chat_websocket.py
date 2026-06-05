# tests/test_chat_websocket.py
"""
Tests for the chat WebSocket transport (WS /chat/ws).

The brain (process_message) and the per-frame DB sessions are mocked — these
tests cover transport concerns only: handshake auth, close codes, frame
routing (message/history/ping/ack_notifications), the connect push, the
ack/typing/reply sequence, and error frames.

Run:  pytest tests/test_chat_websocket.py -v
"""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.routers.chat as chat_router
from app.main import app
from app.services.chat_core import ChatReply
from app.utils.auth import create_access_token

client = TestClient(app)


def patient_token() -> str:
    return create_access_token(
        {"sub": "+237679977660", "type": "patient", "user_id": str(uuid.uuid4())}
    )


def fake_message_row(**over):
    base = dict(
        id=uuid.uuid4(),
        direction="out",
        content="Reminder: ANC visit tomorrow 09:00.",
        message_type="reminder",
        triage_level=None,
        is_read=False,
        created_at=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


class FakeQuery:
    """Chainable stand-in for a SQLAlchemy query."""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def patched_session(rows):
    """Patch chat_router.SessionLocal so every query returns `rows`."""
    session_cls = patch.object(chat_router, "SessionLocal")
    return session_cls, rows


def make_session(patient_row=None, message_rows=()):
    """Fake session: Patient queries hit patient_row, Message queries hit message_rows."""
    session = MagicMock()

    def query(model):
        if model is chat_router.Patient:
            return FakeQuery([patient_row] if patient_row else [])
        return FakeQuery(list(message_rows))

    session.query.side_effect = query
    return session


def _open(ws):
    """Consume the connect push (connected + unread_notifications)."""
    connected = json.loads(ws.receive_text())
    assert connected == {"type": "connected"}
    unread = json.loads(ws.receive_text())
    assert unread["type"] == "unread_notifications"
    return unread


def _read_until_reply(ws):
    """Consume reply_start/delta frames; return (deltas_text, final_reply_frame)."""
    deltas = []
    while True:
        frame = json.loads(ws.receive_text())
        if frame["type"] == "reply_start":
            continue
        if frame["type"] == "delta":
            deltas.append(frame["text"])
            continue
        assert frame["type"] == "reply", frame
        return "".join(deltas), frame


def _expect_close(url: str, expected_code: int):
    with client.websocket_connect(url) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == expected_code


# ── handshake / auth ──────────────────────────────────────────────────────────

def test_ws_missing_token_closes_4401():
    _expect_close("/chat/ws", 4401)


def test_ws_invalid_token_closes_4401():
    _expect_close("/chat/ws?token=not-a-jwt", 4401)


def test_ws_hospital_token_closes_4403():
    token = create_access_token(
        {"sub": "+237600000000", "type": "hospital", "user_id": str(uuid.uuid4())}
    )
    _expect_close(f"/chat/ws?token={token}", 4403)


def test_ws_bearer_header_accepted():
    """Non-browser clients may authenticate with an Authorization header."""
    token = patient_token()
    sc, _ = patched_session([])
    with sc as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery([])
        with client.websocket_connect(
            "/chat/ws", headers={"Authorization": f"Bearer {token}"}
        ) as ws:
            _open(ws)


# ── connect push ──────────────────────────────────────────────────────────────

def test_ws_pushes_unread_notifications_on_connect():
    row = fake_message_row()
    sc, _ = patched_session([row])
    with sc as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery([row])
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            unread = _open(ws)
            assert len(unread["items"]) == 1
            item = unread["items"][0]
            assert item["message_type"] == "reminder"
            assert item["is_read"] is False
            assert item["id"] == str(row.id)


# ── chat message: ack → typing → reply ────────────────────────────────────────

def test_ws_message_sequence_and_reply_contract():
    fake_reply = ChatReply(
        text="Maria, that is common in pregnancy.",
        channel="app",
        triage_level="medium",
    )
    patient_row = SimpleNamespace(id="p1")
    with patch.object(chat_router, "process_message", return_value=fake_reply) as brain, \
         patch.object(chat_router, "SessionLocal") as mock_sc:
        # connect push queries return empty; chat lookup returns the patient
        mock_sc.return_value = make_session(patient_row=patient_row)
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"message": "I have back pain", "client_msg_id": "m-1"}))

            ack = json.loads(ws.receive_text())
            assert ack == {"type": "ack", "client_msg_id": "m-1"}

            typing = json.loads(ws.receive_text())
            assert typing == {"type": "typing"}

            streamed, reply = _read_until_reply(ws)
            assert streamed.strip() == "Maria, that is common in pregnancy."
            assert reply == {
                "type": "reply",
                "client_msg_id": "m-1",
                "reply": "Maria, that is common in pregnancy.",
                "triage_level": "medium",
                "loss_detected": False,
                "is_crisis": False,
            }
            assert brain.call_args.kwargs.get("channel") == "app"


def test_ws_plain_text_frame_tolerated():
    fake_reply = ChatReply(text="ok", channel="app", triage_level="low")
    with patch.object(chat_router, "process_message", return_value=fake_reply), \
         patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value = make_session(patient_row=SimpleNamespace(id="p1"))
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text("just plain text")
            assert json.loads(ws.receive_text())["type"] == "ack"
            assert json.loads(ws.receive_text())["type"] == "typing"
            _, reply = _read_until_reply(ws)
            assert reply["type"] == "reply"


def test_ws_empty_message_returns_error_and_stays_open():
    fake_reply = ChatReply(text="ok", channel="app", triage_level="low")
    with patch.object(chat_router, "process_message", return_value=fake_reply), \
         patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value = make_session(patient_row=SimpleNamespace(id="p1"))
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"message": "   ", "client_msg_id": "m-2"}))
            err = json.loads(ws.receive_text())
            assert err["type"] == "error"
            assert err["client_msg_id"] == "m-2"
            # Connection still usable after the error frame.
            ws.send_text(json.dumps({"message": "real message"}))
            assert json.loads(ws.receive_text())["type"] == "ack"


def test_ws_deactivated_patient_closes_4404():
    with patch.object(chat_router, "SessionLocal") as mock_sc:
        # Empty result for every query → connect push empty, patient lookup None.
        mock_sc.return_value.query.return_value = FakeQuery([])
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"message": "hello"}))
            assert json.loads(ws.receive_text())["type"] == "ack"
            assert json.loads(ws.receive_text())["type"] == "typing"
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
            assert exc.value.code == 4404


# ── history ───────────────────────────────────────────────────────────────────

def test_ws_history_page():
    rows = [
        fake_message_row(direction="in", content="hi", message_type="chat",
                         triage_level="low"),
        fake_message_row(direction="out", content="hello!", message_type="chat"),
    ]
    with patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery(list(rows))
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"action": "history", "limit": 10}))
            page = json.loads(ws.receive_text())
            assert page["type"] == "history"
            assert len(page["items"]) == 2
            assert page["has_more"] is False
            assert page["items"][0]["content"] == "hi"


def test_ws_history_bad_cursor_is_error_frame():
    with patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery([])
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"action": "history", "before": "not-a-date"}))
            err = json.loads(ws.receive_text())
            assert err["type"] == "error"
            assert "before" in err["detail"]


# ── notifications ack ─────────────────────────────────────────────────────────

def test_ws_ack_notifications():
    row = fake_message_row()
    with patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery([row])
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({
                "action": "ack_notifications", "message_ids": [str(row.id)],
            }))
            resp = json.loads(ws.receive_text())
            assert resp == {"type": "notifications_acked", "acknowledged": [str(row.id)]}
            assert row.is_read is True


def test_ws_ack_notifications_rejects_non_list():
    with patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery([])
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"action": "ack_notifications", "message_ids": "x"}))
            assert json.loads(ws.receive_text())["type"] == "error"


# ── heartbeat / unknown action ────────────────────────────────────────────────

def test_ws_ping_pong():
    with patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery([])
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"action": "ping"}))
            assert json.loads(ws.receive_text()) == {"type": "pong"}


def test_ws_unknown_action_is_error_frame():
    with patch.object(chat_router, "SessionLocal") as mock_sc:
        mock_sc.return_value.query.return_value = FakeQuery([])
        with client.websocket_connect(f"/chat/ws?token={patient_token()}") as ws:
            _open(ws)
            ws.send_text(json.dumps({"action": "dance"}))
            err = json.loads(ws.receive_text())
            assert err["type"] == "error"
            assert "dance" in err["detail"]
