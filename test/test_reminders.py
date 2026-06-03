# test/test_reminders.py
"""
Manual smoke test for appointment reminders.

Usage (run from the Backend/ project root):
  source venv/bin/activate
  python -m test.test_reminders

What it does:
  1. Looks up one smartphone patient and one choronko patient by phone.
  2. Creates a near-future appointment for each.
  3. Calls send_24h_reminder directly (no waiting for the scheduler).
  4. For smartphone: verifies an unread reminder message now exists.
     For choronko: prints the SMSResult so you can confirm Queen SMS accepted it.

This bypasses the 15-min scheduler on purpose — it tests the delivery layer.
Set SMARTPHONE_PHONE and CHORONKO_PHONE to real patients in your DB.
"""

from datetime import datetime, timezone, timedelta

from app.core.database import SessionLocal
from app.models.patient import Patient
from app.models.appointment import Appointment
from app.models.message import Message
from app.services.reminder_sender import send_24h_reminder

# --- EDIT THESE to match real patients in your DB ---
SMARTPHONE_PHONE = "237600000001"
CHORONKO_PHONE   = "237600000002"   # use a number you can actually receive SMS on


def _make_appt(db, patient, hours_ahead):
    appt = Appointment(
        patient_id=patient.id,
        hospital_id=patient.hospital_id,
        title="Antenatal check-up (TEST)",
        notes="created by test_reminders.py",
        appointment_datetime=datetime.now(timezone.utc) + timedelta(hours=hours_ahead),
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt


def main():
    db = SessionLocal()
    try:
        sp = db.query(Patient).filter(Patient.phone == SMARTPHONE_PHONE).first()
        ch = db.query(Patient).filter(Patient.phone == CHORONKO_PHONE).first()

        if not sp or not ch:
            print("Could not find both test patients. Check SMARTPHONE_PHONE / CHORONKO_PHONE.")
            print(f"  smartphone found: {bool(sp)} | choronko found: {bool(ch)}")
            return

        print(f"smartphone patient: {sp.name} ({sp.account_type})")
        print(f"choronko patient:   {ch.name} ({ch.account_type})")

        # --- smartphone path ---
        sp_appt = _make_appt(db, sp, hours_ahead=24)
        send_24h_reminder(sp_appt, db)
        unread = db.query(Message).filter(
            Message.patient_id == sp.id,
            Message.direction == "out",
            Message.message_type == "reminder",
            Message.is_read == False,
        ).count()
        print(f"[smartphone] unread reminder messages now: {unread}  (expected >= 1)")
        print(f"[smartphone] reminder_24h_sent flag: {sp_appt.reminder_24h_sent}")

        # --- choronko path (sends a real SMS via Queen SMS) ---
        ch_appt = _make_appt(db, ch, hours_ahead=24)
        try:
            send_24h_reminder(ch_appt, db)
            print(f"[choronko] reminder_24h_sent flag: {ch_appt.reminder_24h_sent}  (expected True)")
            print("[choronko] SMS dispatched — check the phone.")
        except Exception as exc:
            print(f"[choronko] SMS send failed (flag stays False, will retry): {exc}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
