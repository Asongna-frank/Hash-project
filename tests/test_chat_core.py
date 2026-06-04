# tests/test_chat_core.py
"""
Tests for the unified chat core (channel-agnostic brain) + app channel + inbound
SMS (stubbed provider) + phone normalization + multilingual pivot.

Translation, LLM (detect_loss / generate_reply), and sms_service are all mocked —
no network, no DB writes, no credentials.

Run:  pytest tests/test_chat_core.py -v
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services import chat_core, content_store
from app.services.chat_core import ChatReply, process_message
from app.services.sms_service import StubInboundSMSParser, InboundSMS


# ── fakes ─────────────────────────────────────────────────────────────────────

def make_patient(language="en", status="active", pending=False):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="Test Patient",
        language=language,
        account_type="smartphone",
        phone="+237677123456",
        hospital_id="22222222-2222-2222-2222-222222222222",
        status=status,
        pending_loss_confirmation=pending,
        opt_out_status=None,
        paused_until=None,
        risk_level="low",
        risk_level_set_at=None,
        risk_level_set_by=None,
        lmp=date(2025, 11, 1),
        age=28,
        parity=1,
    )


class _FakeMsg(SimpleNamespace):
    pass


@pytest.fixture
def spies(monkeypatch):
    """Patch the brain's collaborators and capture save_inbound/save_outbound calls."""
    state = {"inbound": [], "outbound": []}

    def fake_save_inbound(patient_id, content, triage_level, channel="app",
                          message_type="chat", source_lang=None,
                          provider_message_id=None, flagged_for_review=False):
        m = _FakeMsg(content=content, triage_level=triage_level, channel=channel,
                     message_type=message_type, source_lang=source_lang,
                     provider_message_id=provider_message_id,
                     flagged_for_review=flagged_for_review)
        state["inbound"].append(m)
        return m

    def fake_save_outbound(patient_id, content, channel="app", message_type="chat",
                           source_lang=None):
        m = _FakeMsg(content=content, channel=channel, message_type=message_type,
                     source_lang=source_lang)
        state["outbound"].append(m)
        return m

    monkeypatch.setattr(chat_core, "save_inbound", fake_save_inbound)
    monkeypatch.setattr(chat_core, "save_outbound", fake_save_outbound)

    # Default collaborators — individual tests override as needed.
    state["detect_loss"] = MagicMock(return_value="NOT_A_LOSS")
    state["generate_reply"] = MagicMock(return_value=("EN_REPLY", "low"))
    state["translate"] = MagicMock(side_effect=lambda text, s, t: text)
    state["alert"] = MagicMock()

    monkeypatch.setattr(chat_core, "detect_loss", state["detect_loss"])
    monkeypatch.setattr(chat_core, "generate_reply", state["generate_reply"])
    monkeypatch.setattr(chat_core.translation_service, "translate", state["translate"])
    monkeypatch.setattr(chat_core, "_alert_hospital", state["alert"])
    return state


def fake_db():
    return MagicMock()


# ── 1. App channel: brain called with channel="app", reply in HTTP response ────

def test_app_channel_endpoint_wraps_brain(monkeypatch):
    from app.main import app
    from app.core.database import get_db
    from app.utils.auth import get_current_user
    import app.routers.chat as chat_router

    captured = {}

    def fake_process(patient, text, channel, db, provider_message_id=None):
        captured["channel"] = channel
        captured["text"] = text
        return ChatReply(text="hi back", channel=channel, triage_level="low")

    monkeypatch.setattr(chat_router, "process_message", fake_process)
    monkeypatch.setattr(chat_router, "_get_patient", lambda pid, db: make_patient())

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: {"type": "patient", "user_id": "p1"}
    try:
        client = TestClient(app)
        r = client.post("/chat/message", json={"message": "hello"},
                        headers={"Authorization": "Bearer x"})
        assert r.status_code == 200, r.text
        assert r.json()["reply"] == "hi back"
        assert captured["channel"] == "app"
        assert captured["text"] == "hello"
    finally:
        app.dependency_overrides.clear()


# ── 2. SMS inbound (fake parser): patient resolved, brain called, reply sent ────

def test_sms_inbound_webhook_dispatches_to_brain(monkeypatch):
    from app.main import app
    from app.core.database import get_db
    import app.routers.sms as sms_router

    patient = make_patient()
    fake_parser = MagicMock()
    fake_parser.verify_and_parse.return_value = InboundSMS(
        from_phone="+237677123456", text="hello", provider_message_id="MSG1")
    sent = {}

    monkeypatch.setattr(sms_router, "inbound_sms_parser", fake_parser)
    monkeypatch.setattr(sms_router, "_lookup_patient_by_phone", lambda phone, db: patient)
    monkeypatch.setattr(sms_router, "_is_duplicate", lambda pid, db: False)
    proc = MagicMock(return_value=ChatReply(text="sms reply", channel="sms", triage_level="low"))
    monkeypatch.setattr(sms_router, "process_message", proc)
    monkeypatch.setattr(sms_router.sms_service, "send_sms",
                        lambda to, message: sent.update(to=to, message=message))

    app.dependency_overrides[get_db] = fake_db
    try:
        client = TestClient(app)
        r = client.post("/sms/inbound", content=b"raw")
        assert r.status_code == 200, r.text
        # brain called with channel="sms"
        assert proc.call_args.kwargs["channel"] == "sms"
        # reply sent back via sms_service
        assert sent["message"] == "sms reply"
        assert sent["to"] == patient.phone
    finally:
        app.dependency_overrides.clear()


# ── 3. Same brain both channels: identical fr message → same triage + English ──

def test_no_drift_between_channels(spies):
    spies["translate"].side_effect = lambda text, s, t: ("ENGLISH_TEXT" if t == "en" else "FR_REPLY")
    spies["generate_reply"].return_value = ("EN_REPLY", "medium")

    p_app = make_patient(language="fr")
    p_sms = make_patient(language="fr")

    r_app = process_message(p_app, "bonjour", channel="app", db=fake_db())
    inbound_app = spies["inbound"][-1].content

    r_sms = process_message(p_sms, "bonjour", channel="sms", db=fake_db())
    inbound_sms = spies["inbound"][-1].content

    # Same triage and same stored English content → no app-vs-sms drift.
    assert r_app.triage_level == r_sms.triage_level == "medium"
    assert inbound_app == inbound_sms == "ENGLISH_TEXT"
    # channel echoed back out for routing
    assert r_app.channel == "app" and r_sms.channel == "sms"


# ── 4. Crisis: pre-approved stored message, alert raised, NOT live-translated ──

def test_crisis_uses_stored_message_not_translation(spies):
    spies["detect_loss"].return_value = "CONFIRMED"
    patient = make_patient(language="en")

    reply = process_message(patient, "I lost my baby", channel="sms", db=fake_db())

    assert reply.is_crisis is True
    assert reply.channel == "sms"
    assert reply.text == content_store.get_content("post_loss_opening", "en")
    # alert raised (M6 path)
    assert spies["alert"].called
    # crisis message is served from the store, never live-translated
    assert not spies["translate"].called


# ── 5. Inbound phone normalization happens BEFORE patient lookup ───────────────

def test_inbound_phone_normalized_before_lookup(monkeypatch):
    from app.main import app
    from app.core.database import get_db
    import app.routers.sms as sms_router

    fake_parser = MagicMock()
    fake_parser.verify_and_parse.return_value = InboundSMS(
        from_phone="677123456", text="hi", provider_message_id="MSG5")  # local CM format
    looked_up = {}

    def fake_lookup(phone, db):
        looked_up["phone"] = phone
        return make_patient()

    monkeypatch.setattr(sms_router, "inbound_sms_parser", fake_parser)
    monkeypatch.setattr(sms_router, "_lookup_patient_by_phone", fake_lookup)
    monkeypatch.setattr(sms_router, "_is_duplicate", lambda pid, db: False)
    monkeypatch.setattr(sms_router, "process_message",
                        MagicMock(return_value=ChatReply(text="r", channel="sms")))
    monkeypatch.setattr(sms_router.sms_service, "send_sms", lambda to, message: None)

    app.dependency_overrides[get_db] = fake_db
    try:
        TestClient(app).post("/sms/inbound", content=b"raw")
        # local "677123456" must be normalized to E.164 before lookup
        assert looked_up["phone"] == "+237677123456"
    finally:
        app.dependency_overrides.clear()


# ── 6. Idempotency: same provider_message_id twice → processed once ────────────

def test_inbound_idempotency(monkeypatch):
    from app.main import app
    from app.core.database import get_db
    import app.routers.sms as sms_router

    fake_parser = MagicMock()
    fake_parser.verify_and_parse.return_value = InboundSMS(
        from_phone="+237677123456", text="hi", provider_message_id="DUP")
    proc = MagicMock(return_value=ChatReply(text="r", channel="sms"))

    monkeypatch.setattr(sms_router, "inbound_sms_parser", fake_parser)
    monkeypatch.setattr(sms_router, "_lookup_patient_by_phone", lambda phone, db: make_patient())
    # first call: not duplicate; second call: duplicate
    monkeypatch.setattr(sms_router, "_is_duplicate", MagicMock(side_effect=[False, True]))
    monkeypatch.setattr(sms_router, "process_message", proc)
    monkeypatch.setattr(sms_router.sms_service, "send_sms", lambda to, message: None)

    app.dependency_overrides[get_db] = fake_db
    try:
        client = TestClient(app)
        r1 = client.post("/sms/inbound", content=b"raw")
        r2 = client.post("/sms/inbound", content=b"raw")
        assert r1.json()["status"] == "ok"
        assert r2.json()["status"] == "duplicate"
        assert proc.call_count == 1  # processed exactly once
    finally:
        app.dependency_overrides.clear()


# ── 7. PAUSE/STOP/RESUME short-circuits on both channels, one confirmation ─────

@pytest.mark.parametrize("channel,keyword,expected_status,content_key", [
    ("app", "PAUSE", "paused", "pause_confirmation"),
    ("sms", "STOP", "stopped", "stop_confirmation"),
])
def test_opt_out_short_circuits(spies, channel, keyword, expected_status, content_key):
    patient = make_patient(language="en")
    reply = process_message(patient, keyword, channel=channel, db=fake_db())

    assert patient.opt_out_status == expected_status
    assert reply.text == content_store.get_content(content_key, "en")
    assert reply.channel == channel
    # exactly one confirmation, no further processing
    assert len(spies["outbound"]) == 1
    assert not spies["detect_loss"].called
    assert not spies["generate_reply"].called


# ── 8. Stub parser documents the provider gap; webhook handles it cleanly ──────

def test_stub_parser_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        StubInboundSMSParser().verify_and_parse({}, b"")


def test_webhook_handles_stub_gap_without_crashing():
    from app.main import app
    from app.core.database import get_db
    # default parser is the stub → webhook should return 503, not crash
    app.dependency_overrides[get_db] = fake_db
    try:
        r = TestClient(app, raise_server_exceptions=False).post("/sms/inbound", content=b"raw")
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()


# ── 9. Translate-IN failure fails SAFE: not dropped, triage on original, flagged ──

def test_translate_in_failure_fails_safe(spies):
    spies["translate"].side_effect = RuntimeError("translation down")
    spies["generate_reply"].return_value = ("EN_REPLY", "low")
    patient = make_patient(language="fr")

    reply = process_message(patient, "bonjour je suis malade", channel="app", db=fake_db())

    # message not dropped — inbound saved, flagged for review, original text kept
    assert len(spies["inbound"]) == 1
    assert spies["inbound"][0].flagged_for_review is True
    assert spies["inbound"][0].content == "bonjour je suis malade"
    # triage still attempted on the original text
    spies["detect_loss"].assert_called_once_with("bonjour je suis malade")
    assert spies["generate_reply"].called
    # out-pivot also failed → English fallback delivered (still safe)
    assert reply.text == "EN_REPLY"
