# test/test_tips_api.py
"""
End-to-end test for the Daily Tips feature.

Covers:
  GET  /tips/today              no tip yet → {"tip": null}
  GET  /tips/today              after send → tip object with content
  send_daily_tip() idempotency  second call creates no duplicate
  GET  /tips/today              hospital token → 403
  GET  /tips/today              no auth → 401/403

Usage (run from the Backend/ project root, server already running):
  source venv/bin/activate
  python -m test.test_tips_api
"""

import os
import sys
import uuid
from datetime import date

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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    client = httpx.Client(base_url=BASE_URL, timeout=60.0)

    # --- connectivity ---------------------------------------------------------
    try:
        root = client.get("/")
    except Exception as exc:
        print(f"Could not reach server at {BASE_URL}: {exc}")
        print("Start it first:  uvicorn app.main:app --host 127.0.0.1 --port 8000")
        return 1
    check("server reachable", root.status_code == 200, f"status={root.status_code}")

    # --- setup: hospital + patient --------------------------------------------
    hosp_phone = f"2376{_suffix()}"
    r = client.post("/auth/hospital/signup", json={
        "name": "Tips Test Hospital",
        "phone": hosp_phone,
        "password": PASSWORD,
        "address": "1 Test Lane",
        "personnel_name": "Dr Tips",
        "personnel_contact": "237600000001",
    })
    check("hospital signup 201", r.status_code == 201, f"status={r.status_code} body={r.text}")
    if r.status_code != 201:
        return 1
    hospital_id = r.json()["id"]

    r = client.post("/auth/hospital/login", json={"phone": hosp_phone, "password": PASSWORD})
    check("hospital login 200", r.status_code == 200)
    hospital_token = r.json()["access_token"]

    pat_phone = f"2377{_suffix()}"
    r = client.post("/auth/patient/signup", json={
        "name": "Grace Tip Test",
        "phone": pat_phone,
        "password": PASSWORD,
        "hospital_id": hospital_id,
        "weeks_pregnant_at_signup": 20,
        "age": 27,
    })
    check("patient signup 201", r.status_code == 201, f"status={r.status_code} body={r.text}")
    if r.status_code != 201:
        return 1
    patient_id = r.json()["id"]

    r = client.post("/auth/patient/login", json={"phone": pat_phone, "password": PASSWORD})
    check("patient login 200", r.status_code == 200)
    patient_token = r.json()["access_token"]

    # --- GET /tips/today before tip is sent -----------------------------------
    print("\n[1] Before tip is sent")
    r = client.get("/tips/today", headers=_auth(patient_token))
    check("GET /tips/today returns 200", r.status_code == 200, f"status={r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    check("tip is null before send", body.get("tip") is None, f"body={body}")

    # --- Seed the tip directly via service layer (scheduler fires at 7 AM) ---
    print("\n[2] Seeding tip via service layer")
    from app.core.database import SessionLocal
    from app.models.patient import Patient
    from app.models.message import Message
    from app.services.tip_sender import send_daily_tip

    db = SessionLocal()
    try:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        check("patient found in DB", patient is not None, f"id={patient_id}")
        if patient is None:
            return 1

        send_daily_tip(patient, db)

        # verify the message was persisted
        tip_msg = (
            db.query(Message)
            .filter(
                Message.patient_id == patient_id,
                Message.message_type == "tip",
                Message.direction == "out",
            )
            .first()
        )
        check("tip message saved to DB", tip_msg is not None)
        check("tip has content", bool(tip_msg and tip_msg.content), f"content={tip_msg and tip_msg.content}")
        check("tip channel is app", tip_msg and tip_msg.channel == "app", f"channel={tip_msg and tip_msg.channel}")
        check("tip is_read defaults False", tip_msg and tip_msg.is_read is False)

        if tip_msg:
            print(f"  Generated tip ({len(tip_msg.content)} chars):")
            print(f"    \"{tip_msg.content}\"")

        # --- Idempotency: second call must NOT create a duplicate -------------
        print("\n[3] Idempotency check")
        count_before = (
            db.query(Message)
            .filter(Message.patient_id == patient_id, Message.message_type == "tip")
            .count()
        )
        send_daily_tip(patient, db)
        count_after = (
            db.query(Message)
            .filter(Message.patient_id == patient_id, Message.message_type == "tip")
            .count()
        )
        check("second send_daily_tip does not duplicate", count_after == count_before,
              f"before={count_before} after={count_after}")
    finally:
        db.close()

    # --- GET /tips/today after tip is sent ------------------------------------
    print("\n[4] After tip is sent")
    r = client.get("/tips/today", headers=_auth(patient_token))
    check("GET /tips/today returns 200", r.status_code == 200, f"status={r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    tip_obj = body.get("tip")
    check("tip is non-null after send", tip_obj is not None, f"body={body}")
    check("tip has id field", bool(tip_obj and tip_obj.get("id")))
    check("tip has content field", bool(tip_obj and tip_obj.get("content")))
    check("tip has created_at field", bool(tip_obj and tip_obj.get("created_at")))
    check("tip is_read is False", tip_obj and tip_obj.get("is_read") is False)

    # --- Access control -------------------------------------------------------
    print("\n[5] Access control")
    r = client.get("/tips/today", headers=_auth(hospital_token))
    check("hospital cannot access /tips/today (403)", r.status_code == 403, f"status={r.status_code}")

    r = client.get("/tips/today")
    check("unauthenticated request rejected", r.status_code in (401, 403), f"status={r.status_code}")

    # --- Summary --------------------------------------------------------------
    print("\n" + "=" * 50)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 50)
    client.close()
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
