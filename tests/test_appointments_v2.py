# tests/test_appointments_v2.py
"""
Appointment v2 tests — unified alarm engine, Twilio SMS, hospital CRUD.

Uses:
  - fastapi.testclient.TestClient  (real app, real DB)
  - unittest.mock.patch            (mock sms_service everywhere)
  - freezegun.freeze_time          (deterministic alarm windows)

Run:
  source venv/bin/activate
  pytest tests/test_appointments_v2.py -v
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

from app.main import app
from app.core.database import SessionLocal
from app.models.appointment import Appointment
from app.services.sms_service import SMSResult

# ── helpers ───────────────────────────────────────────────────────────────────

PASSWORD = "TestPass1!"


def uid():
    return uuid.uuid4().hex[:8]


def dt_str(dt: datetime) -> str:
    return dt.isoformat()


def future(hours=24) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def hospital(client):
    phone = f"2376{uid()}"
    r = client.post("/auth/hospital/signup", json={
        "name": "Appt Test Hospital",
        "phone": phone,
        "password": PASSWORD,
        "address": "1 Test Rd",
        "first_personnel": {"name": "Admin", "phone": f"2370{uid()}", "role": "admin"},
    })
    assert r.status_code == 201, r.text
    tok = client.post("/auth/hospital/login",
                      json={"phone": phone, "password": PASSWORD}).json()["access_token"]
    return {**r.json(), "token": tok, "phone": phone}


@pytest.fixture(scope="module")
def hospital_b(client):
    """Second hospital for scoping tests."""
    phone = f"2376{uid()}"
    r = client.post("/auth/hospital/signup", json={
        "name": "Hospital B",
        "phone": phone,
        "password": PASSWORD,
        "address": "2 Other Rd",
        "first_personnel": {"name": "Admin B", "phone": f"2370{uid()}", "role": "admin"},
    })
    assert r.status_code == 201, r.text
    tok = client.post("/auth/hospital/login",
                      json={"phone": phone, "password": PASSWORD}).json()["access_token"]
    return {**r.json(), "token": tok, "phone": phone}


@pytest.fixture(scope="module")
def smartphone_patient(client, hospital):
    phone = f"2377{uid()}"
    r = client.post("/auth/patient/signup", json={
        "name": "Smartphone Patient",
        "phone": phone,
        "password": PASSWORD,
        "hospital_id": hospital["id"],
        "weeks_pregnant_at_signup": 20,
        "age": 28,
    })
    assert r.status_code == 201, r.text
    tok = client.post("/auth/patient/login",
                      json={"phone": phone, "password": PASSWORD}).json()["access_token"]
    return {**r.json(), "token": tok, "phone": phone}


@pytest.fixture(scope="module")
def choronko_patient(client, hospital):
    """A choronko patient for hospital-create flow."""
    phone = f"2377{uid()}"
    r = client.post("/auth/patient/signup", json={
        "name": "Choronko Patient",
        "phone": phone,
        "password": PASSWORD,
        "hospital_id": hospital["id"],
        "weeks_pregnant_at_signup": 18,
        "age": 25,
    })
    assert r.status_code == 201, r.text
    # Patch account_type to "choronko" directly in DB
    db = SessionLocal()
    try:
        from app.models.patient import Patient
        db.query(Patient).filter(Patient.id == r.json()["id"]).update(
            {"account_type": "choronko"}, synchronize_session="fetch"
        )
        db.commit()
    finally:
        db.close()
    return {**r.json(), "phone": phone}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _drain_existing_appointments(patient_id: str) -> None:
    """
    Mark all existing appointments for a patient as already sent.
    Prevents appointments from earlier test runs leaking into the scheduler
    window and causing false call-count assertions.
    """
    db = SessionLocal()
    try:
        db.query(Appointment).filter(
            Appointment.patient_id == patient_id,
        ).update(
            {"alarm_1_sent": True, "alarm_2_sent": True},
            synchronize_session="fetch",
        )
        db.commit()
    finally:
        db.close()


# ── Test 1: Patient create — stored correctly, no confirmation sent ───────────

def test_01_patient_create_appointment(client, smartphone_patient):
    appt_dt  = future(hours=48)
    reminder = future(hours=36)

    r = client.post("/appointments", json={
        "title": "Antenatal Check-up",
        "appointment_datetime": dt_str(appt_dt),
        "reminder_datetime": dt_str(reminder),
    }, headers=_auth(smartphone_patient["token"]))

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created_by"] == "patient"
    assert body["confirmation_sent"] is False
    assert body["alarm_1_sent"] is False
    assert body["alarm_2_sent"] is False
    assert "reminder_datetime" in body
    # appointment must be in the future
    assert datetime.fromisoformat(body["appointment_datetime"]) > datetime.now(timezone.utc)


# ── Test 2: Reminder-after-appointment rejected ───────────────────────────────

def test_02_reminder_after_appointment_rejected(client, smartphone_patient):
    appt_dt  = future(hours=24)
    reminder = future(hours=25)  # AFTER the appointment — invalid

    r = client.post("/appointments", json={
        "title": "Bad Reminder",
        "appointment_datetime": dt_str(appt_dt),
        "reminder_datetime": dt_str(reminder),
    }, headers=_auth(smartphone_patient["token"]))

    assert r.status_code == 422, r.text


# ── Test 3: Hospital create computes reminder_datetime ────────────────────────

def test_03_hospital_create_computes_reminder(client, hospital, choronko_patient):
    appt_dt = future(hours=48)

    with patch("app.routers.hospital_appointments.sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=True, provider_message_id="SMTEST")

        r = client.post("/hospital/appointments", json={
            "patient_phone": choronko_patient["phone"],
            "title": "Prenatal Visit",
            "appointment_datetime": dt_str(appt_dt),
        }, headers=_auth(hospital["token"]))

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created_by"] == "hospital"

    appt_parsed    = datetime.fromisoformat(body["appointment_datetime"])
    reminder_parsed = datetime.fromisoformat(body["reminder_datetime"])

    # reminder_datetime should be exactly 30 minutes before appointment_datetime
    diff = appt_parsed - reminder_parsed
    assert abs(diff.total_seconds() - 1800) < 2, (
        f"Expected 30-min gap, got {diff}"
    )


# ── Test 4: Hospital create sends confirmation SMS ────────────────────────────

def test_04_hospital_create_sends_confirmation(client, hospital, choronko_patient):
    appt_dt = future(hours=72)

    with patch("app.routers.hospital_appointments.sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=True, provider_message_id="SM999")

        r = client.post("/hospital/appointments", json={
            "patient_phone": choronko_patient["phone"],
            "title": "Glucose Test",
            "appointment_datetime": dt_str(appt_dt),
        }, headers=_auth(hospital["token"]))

    assert r.status_code == 201, r.text
    body = r.json()

    # SMS must have been called exactly once (the confirmation)
    mock_sms.send_sms.assert_called_once()
    assert body["confirmation_sent"] is True
    assert body["sms_confirmation_ok"] is True
    assert body["sms_confirmation_error"] is None


# ── Test 5: Hospital scoping — cross-hospital and unknown phone → 404 ─────────

def test_05_hospital_scoping_blocks_cross_hospital(client, hospital_b, choronko_patient):
    """Hospital B cannot create for hospital A's patient — 404 (no ID leakage)."""
    r = client.post("/hospital/appointments", json={
        "patient_phone": choronko_patient["phone"],
        "title": "Cross-hospital Attack",
        "appointment_datetime": dt_str(future(hours=24)),
    }, headers=_auth(hospital_b["token"]))

    assert r.status_code == 404, r.text


def test_05b_unknown_phone_returns_404(client, hospital):
    r = client.post("/hospital/appointments", json={
        "patient_phone": f"2377{uid()}",
        "title": "Ghost Patient",
        "appointment_datetime": dt_str(future(hours=24)),
    }, headers=_auth(hospital["token"]))

    assert r.status_code == 404, r.text


# ── Test 6: Alarm timing — 30-min reminder → alarms at T−60 and T−30 ─────────

def test_06_alarm_window_timing(client, hospital, choronko_patient):
    """
    Hospital-created appointment: reminder_datetime = appt − 30min.
    Alarm 1 fires at reminder_datetime − 30min = appt − 60min.
    Alarm 2 fires at reminder_datetime             = appt − 30min.
    """
    from app.services.scheduler import check_appointment_reminders

    # Create appointment: appt at T, reminder at T−30min
    T = datetime.now(timezone.utc) + timedelta(hours=6)
    reminder_dt = T - timedelta(minutes=30)

    db = SessionLocal()
    try:
        from app.models.patient import Patient
        patient = db.query(Patient).filter(
            Patient.id == choronko_patient["id"]
        ).first()

        appt = Appointment(
            patient_id=patient.id,
            hospital_id=patient.hospital_id,
            title="Timing Test",
            appointment_datetime=T,
            reminder_datetime=reminder_dt,
            created_by="hospital",
        )
        db.add(appt)
        db.commit()
        appt_id = str(appt.id)
    finally:
        db.close()

    # ── Alarm 1 should fire at T−60min (= reminder_dt − 30min) ──────────────
    alarm1_fire_time = T - timedelta(minutes=60)

    with patch("app.services.reminder_sender.sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=True, provider_message_id="A1")
        with freeze_time(alarm1_fire_time):
            check_appointment_reminders()

    db = SessionLocal()
    try:
        appt_reloaded = db.query(Appointment).filter(Appointment.id == appt_id).first()
        assert appt_reloaded.alarm_1_sent is True, "Alarm 1 should have fired at T−60min"
        assert appt_reloaded.alarm_2_sent is False
    finally:
        db.close()

    # ── Alarm 2 should fire at T−30min (= reminder_dt) ───────────────────────
    alarm2_fire_time = T - timedelta(minutes=30)

    with patch("app.services.reminder_sender.sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=True, provider_message_id="A2")
        with freeze_time(alarm2_fire_time):
            check_appointment_reminders()

    db = SessionLocal()
    try:
        appt_reloaded = db.query(Appointment).filter(Appointment.id == appt_id).first()
        assert appt_reloaded.alarm_2_sent is True, "Alarm 2 should have fired at T−30min"
    finally:
        db.close()


# ── Test 7: Idempotent alarms — flags block re-fire ───────────────────────────

def test_07_alarm_idempotent(client, hospital, choronko_patient):
    from app.services.scheduler import check_appointment_reminders

    T = datetime.now(timezone.utc) + timedelta(hours=5)
    reminder_dt = T - timedelta(minutes=30)

    db = SessionLocal()
    try:
        from app.models.patient import Patient
        patient = db.query(Patient).filter(
            Patient.id == choronko_patient["id"]
        ).first()

        appt = Appointment(
            patient_id=patient.id,
            hospital_id=patient.hospital_id,
            title="Idempotent Test",
            appointment_datetime=T,
            reminder_datetime=reminder_dt,
            created_by="hospital",
            alarm_1_sent=True,   # already fired
            alarm_2_sent=True,   # already fired
        )
        db.add(appt)
        db.commit()
        appt_id = str(appt.id)
    finally:
        db.close()

    # Fire the scheduler at the window — neither alarm should trigger again
    fire_time = T - timedelta(minutes=60)
    with patch("app.services.reminder_sender.sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=True)
        with freeze_time(fire_time):
            check_appointment_reminders()
        mock_sms.send_sms.assert_not_called()

    fire_time2 = T - timedelta(minutes=30)
    with patch("app.services.reminder_sender.sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=True)
        with freeze_time(fire_time2):
            check_appointment_reminders()
        mock_sms.send_sms.assert_not_called()


# ── Test 8: SMS failure — *_sent stays False, appointment intact, no crash ───

def test_08_sms_failure_no_crash(client, hospital, choronko_patient):
    from app.services.scheduler import check_appointment_reminders

    T = datetime.now(timezone.utc) + timedelta(hours=4)
    reminder_dt = T - timedelta(minutes=30)

    db = SessionLocal()
    try:
        from app.models.patient import Patient
        patient = db.query(Patient).filter(
            Patient.id == choronko_patient["id"]
        ).first()

        appt = Appointment(
            patient_id=patient.id,
            hospital_id=patient.hospital_id,
            title="SMS Failure Test",
            appointment_datetime=T,
            reminder_datetime=reminder_dt,
            created_by="hospital",
        )
        db.add(appt)
        db.commit()
        appt_id = str(appt.id)
    finally:
        db.close()

    fire_time = T - timedelta(minutes=60)
    with patch("app.services.reminder_sender.sms_service") as mock_sms:
        mock_sms.send_sms.return_value = SMSResult(ok=False, error="Network error")
        with freeze_time(fire_time):
            # Must NOT raise — job catches the error per appointment
            check_appointment_reminders()

    db = SessionLocal()
    try:
        appt_reloaded = db.query(Appointment).filter(Appointment.id == appt_id).first()
        # alarm_1_sent must still be False (so the next pass retries)
        assert appt_reloaded.alarm_1_sent is False
        # Appointment record must still exist and be undeleted
        assert appt_reloaded.is_deleted is False
    finally:
        db.close()


# ── Test 9: Channel routing — choronko → SMS; smartphone → save_outbound ─────

def test_09_channel_routing_choronko_uses_sms(client, hospital, choronko_patient):
    from app.services.scheduler import check_appointment_reminders

    T = datetime.now(timezone.utc) + timedelta(hours=3)
    reminder_dt = T - timedelta(minutes=30)

    db = SessionLocal()
    try:
        from app.models.patient import Patient
        patient = db.query(Patient).filter(
            Patient.id == choronko_patient["id"]
        ).first()
        assert patient.account_type == "choronko", "Patient must be choronko for this test"

        appt = Appointment(
            patient_id=patient.id,
            hospital_id=patient.hospital_id,
            title="Channel Routing Test",
            appointment_datetime=T,
            reminder_datetime=reminder_dt,
            created_by="hospital",
        )
        db.add(appt)
        db.commit()
        appt_id = str(appt.id)
    finally:
        db.close()

    fire_time = T - timedelta(minutes=60)
    with patch("app.services.reminder_sender.sms_service") as mock_sms, \
         patch("app.services.reminder_sender.save_outbound") as mock_save:
        mock_sms.send_sms.return_value = SMSResult(ok=True, provider_message_id="CH1")
        with freeze_time(fire_time):
            check_appointment_reminders()

    # choronko → Twilio SMS must have been called
    mock_sms.send_sms.assert_called_once()
    # save_outbound is also called for the message log entry
    mock_save.assert_called_once()
    # The save_outbound call must NOT have used channel="app"
    call_kwargs = mock_save.call_args.kwargs
    assert call_kwargs.get("channel") == "sms", (
        f"Expected channel=sms for choronko, got {call_kwargs.get('channel')}"
    )


def test_09b_channel_routing_smartphone_uses_inapp(client, smartphone_patient, hospital):
    from app.services.scheduler import check_appointment_reminders
    from app.models.message import Message

    T = datetime.now(timezone.utc) + timedelta(hours=2, minutes=30)
    reminder_dt = T - timedelta(minutes=30)

    db = SessionLocal()
    try:
        from app.models.patient import Patient
        patient = db.query(Patient).filter(
            Patient.id == smartphone_patient["id"]
        ).first()
        assert patient.account_type == "smartphone"

        appt = Appointment(
            patient_id=patient.id,
            hospital_id=patient.hospital_id,
            title="Smartphone Channel Test",
            appointment_datetime=T,
            reminder_datetime=reminder_dt,
            created_by="patient",
        )
        db.add(appt)
        db.commit()
        appt_id = str(appt.id)
    finally:
        db.close()

    fire_time = T - timedelta(minutes=60)
    with patch("app.services.reminder_sender.sms_service") as mock_sms, \
         patch("app.services.reminder_sender.save_outbound") as mock_save:
        mock_save.return_value = MagicMock()
        with freeze_time(fire_time):
            check_appointment_reminders()

    # Smartphone → NO SMS
    mock_sms.send_sms.assert_not_called()
    # save_outbound called with channel="app", message_type="reminder"
    mock_save.assert_called_once()
    call_kwargs = mock_save.call_args.kwargs
    assert call_kwargs.get("channel") == "app"
    assert call_kwargs.get("message_type") == "reminder"
