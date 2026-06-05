# tests/test_m9_post_loss.py
"""
M9 Post-Loss Care tests: paced cadence (day1 → 48h → every3days → weekly),
PHQ-2 offer at week 2 + response capture, crisis-resource SMS, and the
dashboard case view. DB and providers are mocked.

Run:  pytest tests/test_m9_post_loss.py -v
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import chat_core, checkin_sender
from app.services.checkin_sender import _post_loss_pacing
from app.utils.auth import create_access_token

client = TestClient(app)
NOW = datetime.now(timezone.utc)


def make_patient(**over):
    base = dict(
        id=uuid.uuid4(), hospital_id=uuid.uuid4(), name="Maria", phone="+237679977660",
        language="en", status="post_loss", risk_level="high", account_type="smartphone",
        opt_out_status=None, paused_until=None, pending_loss_confirmation=False,
        lmp=date(2026, 1, 1), is_active=True,
        consecutive_missed_checkins=0, missed_checkin_flag=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_case(days_ago=0, **over):
    base = dict(
        id=uuid.uuid4(), patient_id=uuid.uuid4(),
        activated_at=NOW - timedelta(days=days_ago), activated_by="hosp-1",
        opener_sent_at=NOW - timedelta(days=days_ago),
        current_cadence="day1", phq2_offered_at=None, phq2_response=None,
        phq2_responded_at=None, notes=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── paced cadence stages (SRS 2.7.2) ─────────────────────────────────────────

@pytest.mark.parametrize("days,expected_stage,has_interval", [
    (0, "day1", False),        # opener only — no check-ins on day 1
    (1.5, "day1", False),
    (2.1, "48h", True),        # first touch at +48h
    (10, "every3days", True),  # settling
    (25, "weekly", True),      # weekly by week 3+
])
def test_post_loss_pacing_stages(days, expected_stage, has_interval):
    interval, stage = _post_loss_pacing(days)
    assert stage == expected_stage
    assert (interval is not None) == has_interval


def test_post_loss_due_logic_uses_pacing_not_risk():
    """A high-risk post-loss patient on day 1 gets NOTHING (no bombardment)."""
    patient = make_patient(risk_level="high")
    case = make_case(days_ago=0)
    db = MagicMock()
    with patch.object(checkin_sender, "_last_checkin_sent_at", return_value=None), \
         patch("app.services.post_loss.get_case", return_value=case):
        assert checkin_sender._is_checkin_due(patient, db) is False


def test_post_loss_first_checkin_due_at_48h():
    patient = make_patient()
    case = make_case(days_ago=2.5)
    db = MagicMock()
    with patch.object(checkin_sender, "_last_checkin_sent_at", return_value=None), \
         patch("app.services.post_loss.get_case", return_value=case):
        assert checkin_sender._is_checkin_due(patient, db) is True
    assert case.current_cadence == "48h"


def test_post_loss_weekly_not_due_after_3_days():
    patient = make_patient()
    case = make_case(days_ago=30)
    db = MagicMock()
    last = NOW - timedelta(days=3)
    with patch.object(checkin_sender, "_last_checkin_sent_at", return_value=last), \
         patch("app.services.post_loss.get_case", return_value=case):
        assert checkin_sender._is_checkin_due(patient, db) is False
    assert case.current_cadence == "weekly"


# ── PHQ-2 at week 2 ───────────────────────────────────────────────────────────

def test_phq2_replaces_checkin_at_week_2():
    patient = make_patient()
    case = make_case(days_ago=15)  # > 14 days, never offered
    db = MagicMock()
    with patch.object(checkin_sender, "_is_checkin_due", return_value=True), \
         patch.object(checkin_sender, "_update_missed_counter"), \
         patch("app.services.post_loss.get_case", return_value=case), \
         patch.object(checkin_sender, "generate_checkin") as gen, \
         patch.object(checkin_sender, "save_outbound", return_value=MagicMock()), \
         patch.object(checkin_sender, "push_service") as push:
        push.send_push.return_value = SimpleNamespace(ok=True)
        sent = checkin_sender.send_checkin(patient, db)

    assert sent is True
    gen.assert_not_called()                      # the PHQ-2 slot replaces the LLM check-in
    assert case.phq2_offered_at is not None      # offered exactly once
    push_text = push.send_push.call_args.kwargs.get("message")
    assert "two" in push_text and "joy" in push_text  # gentle PHQ-2 wording


def test_phq2_not_offered_twice():
    patient = make_patient()
    case = make_case(days_ago=20, phq2_offered_at=NOW - timedelta(days=5))
    db = MagicMock()
    with patch.object(checkin_sender, "_is_checkin_due", return_value=True), \
         patch.object(checkin_sender, "_update_missed_counter"), \
         patch("app.services.post_loss.get_case", return_value=case), \
         patch.object(checkin_sender, "generate_checkin", return_value="How are you today?") as gen, \
         patch.object(checkin_sender, "save_outbound", return_value=MagicMock()), \
         patch.object(checkin_sender, "push_service") as push:
        push.send_push.return_value = SimpleNamespace(ok=True)
        checkin_sender.send_checkin(patient, db)
    gen.assert_called_once()  # normal check-in resumes after the offer


def test_phq2_response_recorded_from_chat_reply():
    from app.services.post_loss import record_phq2_response_if_pending

    patient = make_patient()
    case = make_case(days_ago=15, phq2_offered_at=NOW - timedelta(days=1))
    db = MagicMock()
    with patch("app.services.post_loss.get_case", return_value=case):
        record_phq2_response_if_pending(db, patient, "Some days are hard but my sister visits")
    assert case.phq2_response == "Some days are hard but my sister visits"
    assert case.phq2_responded_at is not None

    # A second reply must NOT overwrite her recorded response
    with patch("app.services.post_loss.get_case", return_value=case):
        record_phq2_response_if_pending(db, patient, "another message")
    assert case.phq2_response == "Some days are hard but my sister visits"


# ── crisis-resource SMS ───────────────────────────────────────────────────────

def test_crisis_signal_sends_resource_sms_even_to_smartphone():
    patient = make_patient(account_type="smartphone")
    db = MagicMock()
    with patch.object(chat_core, "generate_reply", return_value=("I hear you", "low")), \
         patch.object(chat_core, "save_inbound", return_value=MagicMock()), \
         patch.object(chat_core, "save_outbound", return_value=MagicMock()), \
         patch.object(chat_core, "_get_pregnancy", return_value=None), \
         patch.object(chat_core, "create_alert"), \
         patch("app.services.post_loss.record_phq2_response_if_pending"), \
         patch("app.services.post_loss.sms_service") as sms, \
         patch("app.services.post_loss.push_service") as push:
        sms.send_sms.return_value = SimpleNamespace(ok=True)
        reply = chat_core.process_message(patient, "I want to die", "app", db)

    assert reply.triage_level == "high"
    sms.send_sms.assert_called_once()
    sent_text = sms.send_sms.call_args[0][1]
    assert "112" in sent_text  # hotline numbers present
    push.send_push.assert_called_once()  # belt-and-braces app push


def test_high_physical_flag_post_loss_does_not_send_crisis_sms():
    """Physical red flags alert the hospital but do NOT trigger hotline SMS."""
    patient = make_patient()
    db = MagicMock()
    with patch.object(chat_core, "generate_reply", return_value=("Go to hospital", "high")), \
         patch.object(chat_core, "save_inbound", return_value=MagicMock()), \
         patch.object(chat_core, "save_outbound", return_value=MagicMock()), \
         patch.object(chat_core, "_get_pregnancy", return_value=None), \
         patch.object(chat_core, "create_alert") as alert, \
         patch("app.services.post_loss.record_phq2_response_if_pending"), \
         patch("app.services.post_loss.sms_service") as sms:
        chat_core.process_message(patient, "I am bleeding heavily", "app", db)
    alert.assert_called_once()
    sms.send_sms.assert_not_called()


# ── dashboard case view ───────────────────────────────────────────────────────

def hospital_token(hid):
    return create_access_token({"sub": "+2376", "type": "hospital", "user_id": hid})


def test_post_loss_case_view():
    from app.core.database import get_db as real_get_db

    hid = str(uuid.uuid4())
    patient = make_patient(hospital_id=hid)
    case = make_case(days_ago=16, phq2_offered_at=NOW - timedelta(days=2),
                     phq2_response="Some days are hard", phq2_responded_at=NOW - timedelta(days=1),
                     current_cadence="every3days")
    pregnancy = SimpleNamespace(loss_date=date(2026, 5, 20), ga_at_loss=18,
                                created_at=NOW, outcome="loss")

    db = MagicMock()
    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        if model.__name__ == "Patient":
            q.first.return_value = patient
        elif model.__name__ == "PostLossCase":
            q.first.return_value = case
        else:
            q.first.return_value = pregnancy
        return q
    db.query.side_effect = query

    app.dependency_overrides[real_get_db] = lambda: db
    try:
        resp = client.get(
            f"/hospital/patients/{patient.id}/post-loss",
            headers={"Authorization": f"Bearer {hospital_token(hid)}"},
        )
    finally:
        app.dependency_overrides = {}

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_cadence"] == "every3days"
    assert body["phq2"]["status"] == "responded"
    assert body["phq2"]["response"] == "Some days are hard"
    assert body["loss"]["ga_at_loss"] == 18
    assert body["days_since_activation"] == 16


def test_post_loss_case_view_404_when_no_case():
    from app.core.database import get_db as real_get_db

    hid = str(uuid.uuid4())
    patient = make_patient(hospital_id=hid, status="active")
    db = MagicMock()
    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.first.return_value = patient if model.__name__ == "Patient" else None
        return q
    db.query.side_effect = query
    app.dependency_overrides[real_get_db] = lambda: db
    try:
        resp = client.get(
            f"/hospital/patients/{patient.id}/post-loss",
            headers={"Authorization": f"Bearer {hospital_token(hid)}"},
        )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 404
    assert "No post-loss case" in resp.json()["detail"]
