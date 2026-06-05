# tests/test_voice_realtime.py
"""
Tests for the AI voice features: POST /chat/voice (Whisper -> brain -> TTS)
and the Realtime live-voice endpoints (/chat/realtime/session, /transcript).

OpenAI calls, the brain, and the DB are mocked — transport + safety wiring only.

Run:  pytest tests/test_voice_realtime.py -v
"""

import base64
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.routers.chat as chat_router
from app.core.database import get_db
from app.main import app
from app.services.chat_core import ChatReply
from app.services.voice_service import VoiceServiceError
from app.utils.auth import create_access_token

client = TestClient(app)


def patient_token(pid):
    return create_access_token(
        {"sub": "+237679977660", "type": "patient", "user_id": pid}
    )


def make_patient(**over):
    base = dict(
        id=uuid.uuid4(), hospital_id=uuid.uuid4(), name="Maria", language="en",
        status="active", risk_level="low", lmp=date(2026, 1, 1),
        account_type="smartphone", is_active=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def override_db(patient):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = patient
    app.dependency_overrides[get_db] = lambda: db
    return db


# ── POST /chat/voice ──────────────────────────────────────────────────────────

def test_voice_note_full_flow():
    patient = make_patient()
    db = override_db(patient)
    fake_reply = ChatReply(text="That sounds normal, Maria.", channel="app",
                           triage_level="low")
    try:
        with patch.object(chat_router, "transcribe_audio",
                          return_value="I feel a little tired today") as stt, \
             patch.object(chat_router, "process_message", return_value=fake_reply) as brain, \
             patch.object(chat_router, "synthesize_speech", return_value=b"MP3BYTES") as tts:
            resp = client.post(
                "/chat/voice",
                headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
                files={"audio": ("note.m4a", b"fake-audio-bytes", "audio/m4a")},
            )
    finally:
        app.dependency_overrides = {}

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transcript"] == "I feel a little tired today"
    assert body["reply"] == "That sounds normal, Maria."
    assert body["triage_level"] == "low"
    assert base64.b64decode(body["audio_base64"]) == b"MP3BYTES"
    assert body["audio_mime"] == "audio/mpeg"
    # Same brain, app channel — voice is not a parallel care path.
    assert brain.call_args.kwargs.get("channel") == "app" or brain.call_args[0][2] == "app"
    # Whisper got the patient's language hint
    assert stt.call_args[0][2] == "en"


def test_voice_note_tts_failure_returns_text_only():
    patient = make_patient()
    override_db(patient)
    fake_reply = ChatReply(text="ok", channel="app", triage_level="low")
    try:
        with patch.object(chat_router, "transcribe_audio", return_value="hello"), \
             patch.object(chat_router, "process_message", return_value=fake_reply), \
             patch.object(chat_router, "synthesize_speech", return_value=None):
            resp = client.post(
                "/chat/voice",
                headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
                files={"audio": ("note.mp3", b"bytes", "audio/mpeg")},
            )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 200
    assert resp.json()["audio_base64"] is None
    assert resp.json()["reply"] == "ok"


def test_voice_note_bad_format_422():
    patient = make_patient()
    override_db(patient)
    try:
        resp = client.post(
            "/chat/voice",
            headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
            files={"audio": ("document.pdf", b"%PDF", "application/pdf")},
        )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 422


def test_voice_note_stt_failure_422():
    patient = make_patient()
    override_db(patient)
    try:
        with patch.object(chat_router, "transcribe_audio",
                          side_effect=VoiceServiceError("No speech detected in the audio")):
            resp = client.post(
                "/chat/voice",
                headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
                files={"audio": ("note.wav", b"bytes", "audio/wav")},
            )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 422
    assert "speech" in resp.json()["detail"].lower()


# ── POST /chat/realtime/session ───────────────────────────────────────────────

def test_realtime_session_mints_ephemeral_key():
    patient = make_patient(language="fr", risk_level="high")
    override_db(patient)
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"value": "ek_test123", "expires_at": 1750000000}
    fake_resp.raise_for_status.return_value = None
    try:
        with patch.object(chat_router.httpx, "post", return_value=fake_resp) as post:
            resp = client.post(
                "/chat/realtime/session",
                headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
            )
    finally:
        app.dependency_overrides = {}

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_secret"] == "ek_test123"
    assert body["expires_at"] == 1750000000

    # Patient context + hard rules injected server-side
    sent = post.call_args.kwargs["json"]
    instructions = sent["session"]["instructions"]
    assert "Maria" in instructions
    assert "French" in instructions
    assert "never prescribe" in instructions
    assert sent["expires_after"]["seconds"] == 600


def test_realtime_session_post_loss_tone():
    patient = make_patient(status="post_loss")
    override_db(patient)
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"value": "ek_x", "expires_at": 1}
    fake_resp.raise_for_status.return_value = None
    try:
        with patch.object(chat_router.httpx, "post", return_value=fake_resp) as post:
            resp = client.post(
                "/chat/realtime/session",
                headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
            )
    finally:
        app.dependency_overrides = {}
    instructions = post.call_args.kwargs["json"]["session"]["instructions"]
    assert "pregnancy loss" in instructions
    assert "for the best" in instructions  # banned-phrase guidance present


# ── POST /chat/realtime/transcript ────────────────────────────────────────────

def test_realtime_transcript_red_flag_raises_alert():
    patient = make_patient()
    db = override_db(patient)
    try:
        with patch.object(chat_router, "create_alert") as mock_alert, \
             patch.object(chat_router, "save_inbound", return_value=MagicMock()):
            resp = client.post(
                "/chat/realtime/transcript",
                headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
                json={"text": "I am bleeding heavily since this morning"},
            )
    finally:
        app.dependency_overrides = {}

    assert resp.status_code == 200
    body = resp.json()
    assert body["triage_level"] == "high"
    assert any("bleeding" in f for f in body["red_flags"])
    mock_alert.assert_called_once()
    assert "voice call" in mock_alert.call_args.kwargs["reason"]


def test_realtime_transcript_normal_no_alert():
    patient = make_patient()
    override_db(patient)
    try:
        with patch.object(chat_router, "create_alert") as mock_alert, \
             patch.object(chat_router, "save_inbound", return_value=MagicMock()):
            resp = client.post(
                "/chat/realtime/transcript",
                headers={"Authorization": f"Bearer {patient_token(str(patient.id))}"},
                json={"text": "thank you for the chat, I feel better"},
            )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 200
    assert resp.json()["triage_level"] == "low"
    mock_alert.assert_not_called()


def test_realtime_endpoints_reject_hospital_token():
    htok = create_access_token({"sub": "+2376", "type": "hospital", "user_id": "h1"})
    assert client.post("/chat/realtime/session",
                       headers={"Authorization": f"Bearer {htok}"}).status_code == 403
    assert client.post("/chat/realtime/transcript",
                       headers={"Authorization": f"Bearer {htok}"},
                       json={"text": "x"}).status_code == 403
