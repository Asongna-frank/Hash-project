# test/test_appointments_api.py
"""
End-to-end API test for the Appointments feature.

Exercises every appointments endpoint against a RUNNING server, plus the
notifications endpoints that back the feature. Talks HTTP only (httpx) — it does
not import the app, so run it against `uvicorn app.main:app` on BASE_URL.

Usage (run from the Backend/ project root, server already running):
  source venv/bin/activate
  python -m test.test_appointments_api

Covers:
  POST   /appointments              create (happy path, past-date 400, hospital 403)
  GET    /appointments              list (patient view, hospital view, upcoming_only)
  DELETE /appointments/{id}         soft-delete (owner 200, non-owner 403, filtered from list)
  DELETE /appointments              bulk delete (deleted / not_found / access_denied)
  GET    /notifications/unread      bell/banner poll
  POST   /notifications/acknowledge mark-read

Each run uses fresh uuid-suffixed phone numbers so it is safe to re-run.
"""

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import httpx

BASE_URL = os.environ.get("HASH_BASE_URL", "http://127.0.0.1:8000")
PASSWORD = "TestPass1!"   # satisfies the password strength rules

# --- tiny assertion harness ------------------------------------------------
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
    client = httpx.Client(base_url=BASE_URL, timeout=30.0)

    # --- connectivity ------------------------------------------------------
    try:
        root = client.get("/")
    except Exception as exc:
        print(f"Could not reach server at {BASE_URL}: {exc}")
        print("Start it first:  uvicorn app.main:app --host 127.0.0.1 --port 8000")
        return 1
    check("server reachable", root.status_code == 200, f"status={root.status_code}")

    # --- setup: hospital ---------------------------------------------------
    hosp_phone = f"2376{_suffix()}"
    r = client.post("/auth/hospital/signup", json={
        "name": "Test General Hospital",
        "phone": hosp_phone,
        "password": PASSWORD,
        "address": "123 Test Street",
        "personnel_name": "Dr Test",
        "personnel_contact": "237600000000",
    })
    check("hospital signup 201", r.status_code == 201, f"status={r.status_code} body={r.text}")
    if r.status_code != 201:
        return 1
    hospital_id = r.json()["id"]

    r = client.post("/auth/hospital/login", json={"phone": hosp_phone, "password": PASSWORD})
    check("hospital login 200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    hospital_token = r.json()["access_token"]

    # --- setup: two patients at the same hospital --------------------------
    def make_patient(name: str):
        phone = f"2377{_suffix()}"
        rr = client.post("/auth/patient/signup", json={
            "name": name,
            "phone": phone,
            "password": PASSWORD,
            "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 12,
            "age": 28,
        })
        check(f"patient signup 201 ({name})", rr.status_code == 201,
              f"status={rr.status_code} body={rr.text}")
        if rr.status_code != 201:
            return None, None
        rl = client.post("/auth/patient/login", json={"phone": phone, "password": PASSWORD})
        check(f"patient login 200 ({name})", rl.status_code == 200,
              f"status={rl.status_code} body={rl.text}")
        return rr.json()["id"], rl.json()["access_token"]

    p1_id, p1_token = make_patient("Alice Owner")
    p2_id, p2_token = make_patient("Bob Other")
    if not p1_token or not p2_token:
        return 1

    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    # --- CREATE ------------------------------------------------------------
    r = client.post("/appointments", headers=_auth(p1_token), json={
        "title": "Antenatal check-up",
        "notes": "first visit",
        "appointment_datetime": future,
    })
    check("create appointment 201", r.status_code == 201, f"status={r.status_code} body={r.text}")
    appt = r.json() if r.status_code == 201 else {}
    appt_id = appt.get("id")
    check("create sets hospital_id from patient profile",
          appt.get("hospital_id") == hospital_id,
          f"got={appt.get('hospital_id')} expected={hospital_id}")
    check("create defaults reminder flags False",
          appt.get("reminder_24h_sent") is False and appt.get("reminder_2h_sent") is False,
          f"24h={appt.get('reminder_24h_sent')} 2h={appt.get('reminder_2h_sent')}")

    # past datetime rejected
    r = client.post("/appointments", headers=_auth(p1_token), json={
        "title": "Past appt", "appointment_datetime": past,
    })
    check("create with past datetime 400", r.status_code == 400, f"status={r.status_code}")

    # title too short (schema min_length=2) -> 422
    r = client.post("/appointments", headers=_auth(p1_token), json={
        "title": "x", "appointment_datetime": future,
    })
    check("create with too-short title 422", r.status_code == 422, f"status={r.status_code}")

    # hospital token cannot create
    r = client.post("/appointments", headers=_auth(hospital_token), json={
        "title": "Hospital tries", "appointment_datetime": future,
    })
    check("create as hospital 403", r.status_code == 403, f"status={r.status_code}")

    # no auth -> 403/401
    r = client.post("/appointments", json={"title": "No auth", "appointment_datetime": future})
    check("create without auth rejected", r.status_code in (401, 403), f"status={r.status_code}")

    # --- LIST --------------------------------------------------------------
    r = client.get("/appointments", headers=_auth(p1_token))
    check("patient list 200", r.status_code == 200, f"status={r.status_code}")
    ids = [a["id"] for a in r.json()] if r.status_code == 200 else []
    check("patient list contains created appt", appt_id in ids, f"ids={ids}")

    # patient 2 should NOT see patient 1's appointment
    r = client.get("/appointments", headers=_auth(p2_token))
    p2_ids = [a["id"] for a in r.json()] if r.status_code == 200 else []
    check("other patient does not see appt", appt_id not in p2_ids, f"ids={p2_ids}")

    # hospital sees all of its appointments
    r = client.get("/appointments", headers=_auth(hospital_token))
    check("hospital list 200", r.status_code == 200, f"status={r.status_code}")
    h_ids = [a["id"] for a in r.json()] if r.status_code == 200 else []
    check("hospital sees the appt", appt_id in h_ids, f"ids={h_ids}")

    # upcoming_only includes the future appt
    r = client.get("/appointments", headers=_auth(p1_token), params={"upcoming_only": "true"})
    up_ids = [a["id"] for a in r.json()] if r.status_code == 200 else []
    check("upcoming_only includes future appt", appt_id in up_ids, f"ids={up_ids}")

    # --- DELETE ONE: access control + soft delete --------------------------
    # non-owner patient cannot delete
    r = client.delete(f"/appointments/{appt_id}", headers=_auth(p2_token))
    check("non-owner delete 403", r.status_code == 403, f"status={r.status_code}")

    # owner deletes
    r = client.delete(f"/appointments/{appt_id}", headers=_auth(p1_token))
    check("owner delete 200", r.status_code == 200, f"status={r.status_code} body={r.text}")

    # deleted appt no longer listed
    r = client.get("/appointments", headers=_auth(p1_token))
    ids_after = [a["id"] for a in r.json()] if r.status_code == 200 else []
    check("soft-deleted appt filtered from list", appt_id not in ids_after, f"ids={ids_after}")

    # deleting an already-deleted appt -> 404
    r = client.delete(f"/appointments/{appt_id}", headers=_auth(p1_token))
    check("delete already-deleted 404", r.status_code == 404, f"status={r.status_code}")

    # --- BULK DELETE -------------------------------------------------------
    # create three more owned by patient 1
    bulk_ids = []
    for i in range(3):
        rr = client.post("/appointments", headers=_auth(p1_token), json={
            "title": f"Bulk appt {i}", "appointment_datetime": future,
        })
        if rr.status_code == 201:
            bulk_ids.append(rr.json()["id"])
    check("created 3 appts for bulk test", len(bulk_ids) == 3, f"got={len(bulk_ids)}")

    random_id = str(uuid.uuid4())
    r = client.request("DELETE", "/appointments", headers=_auth(p1_token),
                       json={"ids": bulk_ids[:2] + [random_id]})
    check("bulk delete 200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    body = r.json() if r.status_code == 200 else {}
    check("bulk delete: 2 deleted", sorted(body.get("deleted", [])) == sorted(bulk_ids[:2]),
          f"deleted={body.get('deleted')}")
    check("bulk delete: random id not_found", random_id in body.get("not_found", []),
          f"not_found={body.get('not_found')}")

    # patient 2 attempts to bulk-delete patient 1's remaining appt -> access_denied
    remaining = bulk_ids[2]
    r = client.request("DELETE", "/appointments", headers=_auth(p2_token),
                       json={"ids": [remaining]})
    body = r.json() if r.status_code == 200 else {}
    check("bulk delete: non-owner -> access_denied",
          remaining in body.get("access_denied", []),
          f"access_denied={body.get('access_denied')}")

    # confirm remaining was NOT deleted by the denied attempt
    r = client.get("/appointments", headers=_auth(p1_token))
    ids_now = [a["id"] for a in r.json()] if r.status_code == 200 else []
    check("denied bulk delete did not remove appt", remaining in ids_now, f"ids={ids_now}")

    # bulk delete with empty ids -> 422 (schema min_length=1)
    r = client.request("DELETE", "/appointments", headers=_auth(p1_token), json={"ids": []})
    check("bulk delete empty ids 422", r.status_code == 422, f"status={r.status_code}")

    # --- NOTIFICATIONS -----------------------------------------------------
    r = client.get("/notifications/unread", headers=_auth(p1_token))
    check("unread notifications 200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    check("unread notifications returns a list", isinstance(r.json(), list), f"body={r.text}")

    # hospital cannot read patient notifications
    r = client.get("/notifications/unread", headers=_auth(hospital_token))
    check("hospital unread notifications 403", r.status_code == 403, f"status={r.status_code}")

    # acknowledge (no ids -> empty ack list, still 200)
    r = client.post("/notifications/acknowledge", headers=_auth(p1_token),
                    json={"message_ids": []})
    check("acknowledge 200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    check("acknowledge returns acknowledged list",
          "acknowledged" in (r.json() if r.status_code == 200 else {}),
          f"body={r.text}")

    # --- summary -----------------------------------------------------------
    print("\n" + "=" * 50)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 50)
    client.close()
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
