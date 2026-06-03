# test/test_send_real_reminder.py
"""
ONE-OFF: send a real appointment reminder SMS to a real phone, immediately.

This bypasses the 15-min scheduler and calls send_24h_reminder() directly so the
SMS goes out within seconds. It creates (or reuses) a CHORONKO patient — the
SMS track — because only choronko patients receive SMS; smartphone patients get
an in-app message instead.

Run from the Backend/ project root with the server STOPPED:
  source venv/bin/activate
  python -m test.test_send_real_reminder
"""

import sys
from datetime import datetime, timezone, timedelta

from app.core.database import SessionLocal
from app.models.hospital import Hospital
from app.models.patient import Patient
from app.models.appointment import Appointment
from app.models.message import Message
from app.services.reminder_sender import send_24h_reminder, _compose_24h_sms
from app.services.sms_service import sms_service
from app.utils.pregnancy import compute_lmp_and_edd
from app.utils.auth import hash_password

PHONE = "237679977660"   # +237 679 977 660, Queen SMS "237" prefix (no +)
NAME = "Test User"


def main() -> int:
    db = SessionLocal()
    try:
        hospital = db.query(Hospital).first()
        if not hospital:
            print("No hospital exists in the DB — cannot attach a patient. Create one first.")
            return 1
        print(f"using hospital: {hospital.name} ({hospital.id})")

        # Create or reuse a choronko patient with this phone number
        patient = db.query(Patient).filter(Patient.phone == PHONE).first()
        if patient is None:
            lmp, edd = compute_lmp_and_edd(12)
            patient = Patient(
                name=NAME,
                phone=PHONE,
                hashed_password=hash_password("TestPass1!"),
                hospital_id=hospital.id,
                weeks_pregnant_at_signup=12,
                lmp=lmp,
                edd=edd,
                age=28,
                account_type="choronko",   # SMS track
                status="active",
            )
            db.add(patient)
            db.commit()
            db.refresh(patient)
            print(f"created choronko patient: {patient.name} ({patient.phone})")
        else:
            if patient.account_type != "choronko":
                patient.account_type = "choronko"
                db.commit()
            print(f"reusing patient: {patient.name} ({patient.phone}) [{patient.account_type}]")

        # Create an appointment 24h out so the 24h ("tomorrow") wording is correct
        appt = Appointment(
            patient_id=patient.id,
            hospital_id=patient.hospital_id,
            title="Antenatal check-up",
            notes="real SMS reminder test",
            appointment_datetime=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        print(f"created appointment: {appt.id}  for {appt.appointment_datetime.isoformat()}")
        print(f"SMS text to be sent:\n  {_compose_24h_sms(patient.name, appt)}")
        print(f"Queen SMS sender id: {sms_service._sender_id} | base: {sms_service._base_url}")

        # Fire the 24h reminder NOW — sends the SMS via Queen SMS, logs it, sets flag
        try:
            send_24h_reminder(appt, db)
        except Exception as exc:
            print(f"\nSEND FAILED: {exc}")
            print("(reminder_24h_sent left False so the scheduler would retry in-window)")
            return 1

        db.refresh(appt)
        logged = db.query(Message).filter(
            Message.patient_id == patient.id,
            Message.channel == "sms",
            Message.message_type == "reminder",
        ).count()

        print("\nSUCCESS")
        print(f"  Queen SMS accepted the message — check {PHONE}")
        print(f"  reminder_24h_sent = {appt.reminder_24h_sent}")
        print(f"  sms reminder messages logged in history = {logged}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
