# test/test_checkins.py
"""
End-to-end test for the Proactive Wellness Check-in feature.

Covers:
  send_checkin()  first call        → message persisted with correct fields
  send_checkin()  idempotency       → no duplicate within active interval (20 h high-risk)
  send_checkin()  after 25 h        → new check-in sent (daily interval elapsed)
  send_checkin()  medium 5 days     → skipped (< 6.5-day weekly window)
  send_checkin()  medium 8 days     → sent (> 6.5-day weekly window)
  send_checkin()  low milestone wk  → sent inside fortnightly window (milestone override)
  send_checkin()  silenced patient  → skipped
  send_checkin()  pending_loss      → skipped
  send_checkin()  post_loss patient → grief-support content sent

Usage (run from the Backend/ project root, server must be running):
  source venv/bin/activate
  python -m test.test_checkins
"""

import os
import sys
import uuid
from datetime import date, datetime, timezone, timedelta

import httpx

BASE_URL = os.environ.get("HASH_BASE_URL", "http://127.0.0.1:8000")
PASSWORD = "TestPass1!"

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


# ── DB helpers ──────────────────────────────────────────────────────────────

def _clear_checkins(patient_id, db) -> None:
    """Hard-delete all checkin messages for this patient (test isolation only)."""
    from app.models.message import Message
    db.query(Message).filter(
        Message.patient_id == patient_id,
        Message.message_type == "checkin",
    ).delete(synchronize_session="fetch")
    db.commit()
    db.expire_all()


def _plant_checkin(patient_id, age: timedelta, db) -> None:
    """Insert a dummy out-bound checkin at a controlled age, no LLM call."""
    from app.models.message import Message
    msg = Message(
        id=uuid.uuid4(),
        patient_id=patient_id,
        content="[test placeholder]",
        direction="out",
        channel="app",
        message_type="checkin",
        is_read=False,
        created_at=datetime.now(timezone.utc) - age,
    )
    db.add(msg)
    db.commit()
    db.expire_all()


def _force_lmp_to_week(patient, week: int, db) -> None:
    """Update patient's LMP so that today is gestational week `week`."""
    from app.models.patient import Patient as PatientModel
    db.query(PatientModel).filter(PatientModel.id == patient.id).update(
        {"lmp": date.today() - timedelta(weeks=week)},
        synchronize_session="fetch",
    )
    db.commit()
    db.expire_all()
    db.refresh(patient)


def _checkin_count(patient_id, db) -> int:
    from app.models.message import Message
    return (
        db.query(Message)
        .filter(
            Message.patient_id == patient_id,
            Message.message_type == "checkin",
            Message.direction == "out",
        )
        .count()
    )


def main() -> int:
    # ── server probe ──────────────────────────────────────────────────────────
    try:
        r = httpx.get(f"{BASE_URL}/", timeout=5)
        check("server reachable", r.status_code == 200, f"status={r.status_code}")
    except Exception as exc:
        print(f"  FAIL  server not reachable: {exc}")
        print(f"  Start it: uvicorn app.main:app --host 127.0.0.1 --port 8000")
        return 1

    # ── setup: hospital + patient via HTTP ─────────────────────────────────────
    client = httpx.Client(base_url=BASE_URL, timeout=60.0)
    try:
        hosp_phone = f"2376{_suffix()}"
        r = client.post("/auth/hospital/signup", json={
            "name": "Checkin Test Hospital",
            "phone": hosp_phone,
            "password": PASSWORD,
            "address": "2 Test Lane",
            "personnel_name": "Dr Checkin",
            "personnel_contact": "237600000002",
        })
        check("hospital signup 201", r.status_code == 201, f"status={r.status_code} body={r.text}")
        if r.status_code != 201:
            return 1
        hospital_id = r.json()["id"]

        r = client.post("/auth/hospital/login", json={"phone": hosp_phone, "password": PASSWORD})
        check("hospital login 200", r.status_code == 200)

        pat_phone = f"2377{_suffix()}"
        r = client.post("/auth/patient/signup", json={
            "name": "Amina Check Test",
            "phone": pat_phone,
            "password": PASSWORD,
            "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 22,
            "age": 28,
        })
        check("patient signup 201", r.status_code == 201, f"status={r.status_code} body={r.text}")
        if r.status_code != 201:
            return 1
        patient_id = r.json()["id"]
    finally:
        client.close()

    from app.core.database import SessionLocal
    from app.models.patient import Patient
    from app.models.message import Message
    from app.services.checkin_sender import send_checkin, _is_checkin_due

    db = SessionLocal()
    try:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        check("patient found in DB", patient is not None)
        if patient is None:
            return 1

        # ── Section 1: High-risk patient — first check-in ─────────────────────
        print("\n[1] High-risk patient — first check-in")
        _clear_checkins(patient_id, db)
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"risk_level": "high", "status": "active"}, synchronize_session="fetch"
        )
        db.commit()
        db.refresh(patient)

        sent = send_checkin(patient, db)
        check("send_checkin returns True (was sent)", sent is True)

        msg = (
            db.query(Message)
            .filter(
                Message.patient_id == patient_id,
                Message.message_type == "checkin",
                Message.direction == "out",
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        check("checkin message saved to DB", msg is not None)
        check("checkin has content", bool(msg and msg.content and msg.content != "[test placeholder]"))
        check("checkin channel is app", msg and msg.channel == "app",
              f"channel={msg and msg.channel}")
        check("checkin is_read defaults False", msg and msg.is_read is False)
        check("checkin message_type is checkin", msg and msg.message_type == "checkin")

        if msg:
            print(f"  Generated check-in ({len(msg.content)} chars):")
            print(f"    \"{msg.content}\"")

        # ── Section 2: Idempotency (within the 20-hour daily window) ──────────
        print("\n[2] Idempotency — second call within 20 h must not duplicate")
        count_before = _checkin_count(patient_id, db)
        sent2 = send_checkin(patient, db)
        count_after = _checkin_count(patient_id, db)
        check("second send_checkin returns False (skipped)", sent2 is False)
        check("no duplicate message created", count_after == count_before,
              f"before={count_before} after={count_after}")

        # ── Section 3: After 25 h — daily interval elapsed → new check-in ─────
        print("\n[3] After 25 h elapsed — high-risk daily interval fires again")
        _clear_checkins(patient_id, db)
        _plant_checkin(patient_id, timedelta(hours=25), db)
        db.refresh(patient)

        count_before = _checkin_count(patient_id, db)
        sent3 = send_checkin(patient, db)
        count_after = _checkin_count(patient_id, db)
        check("send_checkin returns True after 25 h", sent3 is True)
        check("new message created after interval", count_after == count_before + 1,
              f"before={count_before} after={count_after}")

        # ── Section 4: Medium-risk interval logic ─────────────────────────────
        print("\n[4] Medium-risk — 5 days ago → NOT due; 8 days ago → due")
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"risk_level": "medium"}, synchronize_session="fetch"
        )
        db.commit()
        db.refresh(patient)

        # Plant a checkin 5 days ago → interval (6.5 d) not yet elapsed → not due
        _clear_checkins(patient_id, db)
        _plant_checkin(patient_id, timedelta(days=5), db)
        db.refresh(patient)
        check("medium-risk not due at 5 days", _is_checkin_due(patient, db) is False,
              f"risk={patient.risk_level}")

        # Plant a checkin 8 days ago → interval (6.5 d) elapsed → due
        _clear_checkins(patient_id, db)
        _plant_checkin(patient_id, timedelta(days=8), db)
        db.refresh(patient)
        check("medium-risk due at 8 days", _is_checkin_due(patient, db) is True,
              f"risk={patient.risk_level}")

        count_before = _checkin_count(patient_id, db)
        sent4 = send_checkin(patient, db)
        count_after = _checkin_count(patient_id, db)
        check("send_checkin sends when medium-risk interval elapsed", sent4 is True)
        check("message count increased after medium-risk send", count_after == count_before + 1,
              f"before={count_before} after={count_after}")

        # ── Section 5: Low-risk milestone week override ───────────────────────
        print("\n[5] Low-risk milestone week (week 20) — sends inside fortnightly window")
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"risk_level": "low"}, synchronize_session="fetch"
        )
        db.commit()

        # Set patient at gestational week 20 (anatomy scan milestone)
        _force_lmp_to_week(patient, 20, db)

        # Plant a checkin 8 days ago — inside the 13-day fortnightly window but
        # beyond the 7-day milestone guard, so the milestone override fires
        _clear_checkins(patient_id, db)
        _plant_checkin(patient_id, timedelta(days=8), db)
        db.refresh(patient)

        check("low-risk at milestone week 20 IS due (8 d > 7 d guard)",
              _is_checkin_due(patient, db) is True,
              f"risk={patient.risk_level}")

        count_before = _checkin_count(patient_id, db)
        sent5 = send_checkin(patient, db)
        count_after = _checkin_count(patient_id, db)
        check("milestone check-in sent", sent5 is True)
        check("message count increased for milestone", count_after == count_before + 1,
              f"before={count_before} after={count_after}")

        # Immediately after the milestone send, it must NOT fire again
        check("milestone NOT due immediately after send",
              _is_checkin_due(patient, db) is False)

        # ── Section 6: Silenced patient ───────────────────────────────────────
        print("\n[6] Silenced patient (opt_out_status=stopped) — must be skipped")
        _clear_checkins(patient_id, db)
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"opt_out_status": "stopped", "risk_level": "high"}, synchronize_session="fetch"
        )
        db.commit()
        db.refresh(patient)

        count_before = _checkin_count(patient_id, db)
        sent6 = send_checkin(patient, db)
        count_after = _checkin_count(patient_id, db)
        check("silenced patient: send_checkin returns False", sent6 is False)
        check("silenced patient: no message created", count_after == count_before,
              f"before={count_before} after={count_after}")

        # restore
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"opt_out_status": None}, synchronize_session="fetch"
        )
        db.commit()
        db.refresh(patient)

        # ── Section 7: pending_loss_confirmation patient ──────────────────────
        print("\n[7] pending_loss_confirmation=True — must be skipped")
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"pending_loss_confirmation": True}, synchronize_session="fetch"
        )
        db.commit()
        db.refresh(patient)

        count_before = _checkin_count(patient_id, db)
        sent7 = send_checkin(patient, db)
        count_after = _checkin_count(patient_id, db)
        check("pending_loss_confirmation: send_checkin returns False", sent7 is False)
        check("pending_loss_confirmation: no message created", count_after == count_before)

        # restore
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"pending_loss_confirmation": False}, synchronize_session="fetch"
        )
        db.commit()
        db.refresh(patient)

        # ── Section 8: Post-loss patient ──────────────────────────────────────
        print("\n[8] Post-loss patient — grief-support check-in sent")
        _clear_checkins(patient_id, db)
        db.query(Patient).filter(Patient.id == patient_id).update(
            {"status": "post_loss", "risk_level": "high"}, synchronize_session="fetch"
        )
        db.commit()
        db.refresh(patient)

        count_before = _checkin_count(patient_id, db)
        sent8 = send_checkin(patient, db)
        count_after = _checkin_count(patient_id, db)
        check("post_loss patient: send_checkin returns True", sent8 is True)
        check("post_loss patient: message count increased", count_after == count_before + 1,
              f"before={count_before} after={count_after}")

        last_msg = (
            db.query(Message)
            .filter(
                Message.patient_id == patient_id,
                Message.message_type == "checkin",
                Message.direction == "out",
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        check("post_loss checkin has content", bool(last_msg and last_msg.content))
        if last_msg:
            print(f"  Post-loss check-in ({len(last_msg.content)} chars):")
            print(f"    \"{last_msg.content}\"")

    finally:
        db.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 50)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
