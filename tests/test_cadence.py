# tests/test_cadence.py
"""
Unit tests for check-in cadence logic and missed-response escalation.

Uses freezegun for deterministic date/time and unittest.mock for the DB session.
No server or real DB required.

Run:  pytest tests/test_cadence.py -v
"""

import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from app.core.risk_config import CHECK_IN_CADENCE_DAYS, MISSED_CHECKIN_ESCALATION
from app.services.checkin_sender import (
    _is_checkin_due,
    _is_silenced,
    _update_missed_counter,
    _RISK_INTERVALS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _patient(
    risk_level="high",
    status="active",
    opt_out_status=None,
    paused_until=None,
    pending_loss_confirmation=False,
    consecutive_missed_checkins=0,
    missed_checkin_flag=False,
    account_type="smartphone",
    lmp=None,
):
    from datetime import date
    p = SimpleNamespace(
        id=uuid.uuid4(),
        risk_level=risk_level,
        status=status,
        opt_out_status=opt_out_status,
        paused_until=paused_until,
        pending_loss_confirmation=pending_loss_confirmation,
        consecutive_missed_checkins=consecutive_missed_checkins,
        missed_checkin_flag=missed_checkin_flag,
        account_type=account_type,
        lmp=lmp or (date.today() - timedelta(weeks=20)),
        phone="237600000001",
    )
    return p


def _mock_db_last_checkin(last_sent_at):
    """Return a mock Session whose checkin query returns a message with created_at=last_sent_at."""
    db = MagicMock()
    if last_sent_at is None:
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    else:
        msg = SimpleNamespace(created_at=last_sent_at)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = msg
    return db


def _mock_db_no_checkin():
    return _mock_db_last_checkin(None)


NOW = datetime(2026, 6, 4, 8, 0, 0, tzinfo=timezone.utc)


# ── Test 9: Cadence mapping from config ──────────────────────────────────────

def test_cadence_high_from_config():
    # SRS acceptance criteria: high-risk patients are checked on DAILY.
    assert CHECK_IN_CADENCE_DAYS["high"] == 1
    assert _RISK_INTERVALS["high"] == timedelta(days=1)


def test_cadence_medium_from_config():
    assert CHECK_IN_CADENCE_DAYS["medium"] == 7
    assert _RISK_INTERVALS["medium"] == timedelta(days=7)


def test_cadence_low_from_config():
    assert CHECK_IN_CADENCE_DAYS["low"] == 14
    assert _RISK_INTERVALS["low"] == timedelta(days=14)


def test_cadence_values_not_literals():
    """Intervals are built from config, so changing config changes the intervals."""
    import app.core.risk_config as cfg
    import app.services.checkin_sender as sender
    # If the config value equals what we expect, the interval must match
    for risk, days in cfg.CHECK_IN_CADENCE_DAYS.items():
        assert sender._RISK_INTERVALS[risk] == timedelta(days=days)


# ── Test 10: Due calculation ──────────────────────────────────────────────────

@freeze_time(NOW)
def test_high_risk_due_after_3_days():
    last = NOW - timedelta(days=3)
    patient = _patient(risk_level="high")
    db = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient, db) is True


@freeze_time(NOW)
def test_high_risk_not_due_after_2_days():
    # Daily cadence: 12 hours since the last check-in -> not due yet.
    last = NOW - timedelta(hours=12)
    patient = _patient(risk_level="high")
    db = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient, db) is False


@freeze_time(NOW)
def test_medium_risk_due_after_7_days():
    last = NOW - timedelta(days=7)
    patient = _patient(risk_level="medium")
    db = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient, db) is True


@freeze_time(NOW)
def test_medium_risk_not_due_after_5_days():
    last = NOW - timedelta(days=5)
    patient = _patient(risk_level="medium")
    db = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient, db) is False


@freeze_time(NOW)
def test_low_risk_due_after_14_days():
    last = NOW - timedelta(days=14)
    patient = _patient(risk_level="low")
    db = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient, db) is True


@freeze_time(NOW)
def test_no_prior_checkin_always_due():
    patient = _patient(risk_level="medium")
    db = _mock_db_no_checkin()
    assert _is_checkin_due(patient, db) is True


# ── Test 11: Risk level change recompute ──────────────────────────────────────

@freeze_time(NOW)
def test_risk_change_medium_to_high_makes_due():
    """
    Last check-in 4 days ago.
    medium interval = 7 days → not due (4 < 7).
    high   interval = 3 days → due    (4 >= 3).
    """
    last = NOW - timedelta(days=4)

    patient_medium = _patient(risk_level="medium")
    db_medium = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient_medium, db_medium) is False

    patient_high = _patient(risk_level="high")
    db_high = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient_high, db_high) is True


@freeze_time(NOW)
def test_risk_change_high_to_low_skips_premature():
    """
    Last check-in 4 days ago.
    high interval = 3 days → would be due.
    low  interval = 14 days → not due (4 < 14).
    """
    last = NOW - timedelta(days=4)

    patient_high = _patient(risk_level="high")
    db_high = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient_high, db_high) is True

    patient_low = _patient(risk_level="low")
    db_low = _mock_db_last_checkin(last)
    assert _is_checkin_due(patient_low, db_low) is False


# ── Test 12: Missed check-in escalation ──────────────────────────────────────

def _db_with_checkin_and_reply(last_checkin_at, has_reply: bool):
    """
    Mock DB that:
    - Returns last_checkin_at for the checkin query (order_by().first())
    - Returns a row or None for the inbound-reply query (filter().first())
    """
    db = MagicMock()

    # Two different query chains: checkin (direction=="out") and reply (direction=="in")
    # We discriminate by call order — first call = last checkin, second = reply check.
    checkin_msg = SimpleNamespace(created_at=last_checkin_at)
    reply_msg   = SimpleNamespace(created_at=last_checkin_at + timedelta(hours=1)) if has_reply else None

    call_count = {"n": 0}

    def first_side_effect():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return checkin_msg      # last checkin lookup
        return reply_msg            # reply lookup

    db.query.return_value.filter.return_value.order_by.return_value.first.side_effect = first_side_effect
    db.query.return_value.filter.return_value.first.side_effect = first_side_effect

    return db


@freeze_time(NOW)
def test_missed_escalation_high_risk_flags_at_threshold():
    """
    High-risk threshold = 3.
    Simulate 3 missed check-ins: flag must be set on the 3rd.
    """
    last = NOW - timedelta(days=1)
    patient = _patient(risk_level="high", consecutive_missed_checkins=0)

    for i in range(1, 4):
        db = MagicMock()
        # checkin exists, no reply
        checkin_msg = SimpleNamespace(created_at=last)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = checkin_msg
        db.query.return_value.filter.return_value.first.return_value = None

        _update_missed_counter(patient, db)

        if i < 3:
            assert patient.missed_checkin_flag is False, f"flag should not be set at missed={i}"
        else:
            assert patient.missed_checkin_flag is True, "flag must be set at missed=3"


@freeze_time(NOW)
def test_missed_escalation_flag_set_only_once():
    """Flag is set once at threshold; subsequent misses do not unset and re-set it."""
    last = NOW - timedelta(days=1)
    checkin_msg = SimpleNamespace(created_at=last)

    patient = _patient(
        risk_level="high",
        consecutive_missed_checkins=MISSED_CHECKIN_ESCALATION["high"],
        missed_checkin_flag=True,  # already flagged
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = checkin_msg
    db.query.return_value.filter.return_value.first.return_value = None

    _update_missed_counter(patient, db)

    # counter increments, flag stays True (not toggled)
    assert patient.missed_checkin_flag is True
    assert patient.consecutive_missed_checkins == MISSED_CHECKIN_ESCALATION["high"] + 1


@freeze_time(NOW)
def test_reply_resets_counter_and_clears_flag():
    """Any inbound reply resets consecutive_missed_checkins to 0 and clears flag."""
    last = NOW - timedelta(days=1)
    checkin_msg = SimpleNamespace(created_at=last)
    reply_msg   = SimpleNamespace(created_at=last + timedelta(hours=2))

    patient = _patient(
        risk_level="high",
        consecutive_missed_checkins=3,
        missed_checkin_flag=True,
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = checkin_msg
    db.query.return_value.filter.return_value.first.return_value = reply_msg

    _update_missed_counter(patient, db)

    assert patient.consecutive_missed_checkins == 0
    assert patient.missed_checkin_flag is False


@freeze_time(NOW)
def test_medium_risk_escalates_at_2():
    """Medium-risk threshold = 2. Flag must appear on the 2nd miss, not the 1st."""
    last = NOW - timedelta(days=1)
    checkin_msg = SimpleNamespace(created_at=last)

    patient = _patient(risk_level="medium", consecutive_missed_checkins=0)

    for i in range(1, 3):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = checkin_msg
        db.query.return_value.filter.return_value.first.return_value = None

        _update_missed_counter(patient, db)

        if i < 2:
            assert patient.missed_checkin_flag is False
        else:
            assert patient.missed_checkin_flag is True


@freeze_time(NOW)
def test_low_risk_never_escalates():
    """Low risk is not in MISSED_CHECKIN_ESCALATION — no flag should ever be set."""
    last = NOW - timedelta(days=1)
    checkin_msg = SimpleNamespace(created_at=last)

    patient = _patient(risk_level="low", consecutive_missed_checkins=0)

    # Miss 10 check-ins
    for _ in range(10):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = checkin_msg
        db.query.return_value.filter.return_value.first.return_value = None
        _update_missed_counter(patient, db)

    assert patient.missed_checkin_flag is False


# ── Test 13: Silence logic ────────────────────────────────────────────────────

def test_silenced_stopped():
    patient = _patient(opt_out_status="stopped")
    assert _is_silenced(patient) is True


@freeze_time(NOW)
def test_silenced_paused_active():
    future = NOW + timedelta(days=3)
    patient = _patient(opt_out_status="paused", paused_until=future)
    assert _is_silenced(patient) is True


@freeze_time(NOW)
def test_paused_expired_not_silenced():
    past = NOW - timedelta(hours=1)
    patient = _patient(opt_out_status="paused", paused_until=past)
    assert _is_silenced(patient) is False


def test_not_silenced_by_default():
    patient = _patient()
    assert _is_silenced(patient) is False
