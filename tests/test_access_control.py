# tests/test_access_control.py
"""
Integration tests that prove the access-control model holds.

Covers:
  1.  Hospital signup creates first personnel (role=admin)
  2.  Hospital can add personnel; list returns all
  3.  Cross-hospital personnel PATCH/DELETE → 404
  4.  Personnel hard-delete removes the row from the DB entirely
  5.  Patient can GET/PATCH own record; other patient's record → 403
  6.  Patient calling GET /patients → 403
  7.  Hospital sees only its own patients in GET /patients
  8.  Hospital requesting another hospital's patient by id → 404
  9.  Cross-hospital patient PATCH/DELETE → 404
  10. Patient soft-delete: is_active=False, absent from list, row + history intact
  11. Hospital soft-delete: absent from public list, row remains in DB
  12. Patient cannot reach hospital or personnel endpoints (403)

Run:
  source venv/bin/activate
  pytest tests/test_access_control.py -v
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal

# ── Helpers ───────────────────────────────────────────────────────────────────

PASSWORD = "TestPass1!"


def uid():
    return uuid.uuid4().hex[:8]


def _signup_hospital(client, suffix=""):
    phone = f"2376{uid()}"
    r = client.post("/auth/hospital/signup", json={
        "name": f"Test Hospital {suffix}",
        "phone": phone,
        "password": PASSWORD,
        "address": f"{suffix} Test Road",
        "first_personnel": {
            "name": f"Head Admin {suffix}",
            "phone": f"2370{uid()}",
            "role": "admin",
        },
    })
    assert r.status_code == 201, r.text
    login = client.post("/auth/hospital/login",
                        json={"phone": phone, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {**r.json(), "token": login.json()["access_token"], "phone": phone}


def _signup_patient(client, hospital_id, suffix=""):
    phone = f"2377{uid()}"
    r = client.post("/auth/patient/signup", json={
        "name": f"Test Patient {suffix}",
        "phone": phone,
        "password": PASSWORD,
        "hospital_id": hospital_id,
        "weeks_pregnant_at_signup": 20,
        "age": 28,
    })
    assert r.status_code == 201, r.text
    login = client.post("/auth/patient/login",
                        json={"phone": phone, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {**r.json(), "token": login.json()["access_token"], "phone": phone}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── Module-scope fixtures: create persistent test state once per module ────────

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def hospital_a(client):
    return _signup_hospital(client, "A")


@pytest.fixture(scope="module")
def hospital_b(client):
    return _signup_hospital(client, "B")


@pytest.fixture(scope="module")
def patient_a(client, hospital_a):
    return _signup_patient(client, hospital_a["id"], "A")


@pytest.fixture(scope="module")
def patient_b(client, hospital_b):
    return _signup_patient(client, hospital_b["id"], "B")


# ── Test 1: Hospital signup creates first personnel ───────────────────────────

def test_01_signup_creates_personnel(hospital_a):
    db = SessionLocal()
    try:
        from app.models.personnel import Personnel
        rows = (
            db.query(Personnel)
            .filter(Personnel.hospital_id == hospital_a["id"])
            .all()
        )
        assert len(rows) >= 1
        assert rows[0].role == "admin"
        assert rows[0].name == "Head Admin A"
    finally:
        db.close()


# ── Test 2: Add personnel; list returns them ──────────────────────────────────

def test_02_add_and_list_personnel(client, hospital_a):
    hosp_id = hospital_a["id"]
    token = hospital_a["token"]

    # Add 2 more personnel
    for role in ["doctor", "midwife"]:
        r = client.post(f"/hospitals/{hosp_id}/personnel",
                        json={"name": f"Dr {role}", "phone": f"2371{uid()}", "role": role},
                        headers=_auth(token))
        assert r.status_code == 201, r.text
        assert r.json()["hospital_id"] == hosp_id

    # List — should now be at least 3 (1 from signup + 2 added)
    r = client.get(f"/hospitals/{hosp_id}/personnel", headers=_auth(token))
    assert r.status_code == 200
    assert len(r.json()) >= 3


# ── Test 3: Cross-hospital personnel PATCH/DELETE blocked ─────────────────────

def test_03_cross_hospital_personnel_blocked(client, hospital_a, hospital_b):
    # Get a personnel id belonging to hospital A
    db = SessionLocal()
    try:
        from app.models.personnel import Personnel
        p = (db.query(Personnel)
             .filter(Personnel.hospital_id == hospital_a["id"])
             .first())
        personnel_id = str(p.id)
    finally:
        db.close()

    # Hospital B tries to PATCH hospital A's personnel → 404 (not 403)
    r = client.patch(f"/personnel/{personnel_id}",
                     json={"name": "Hacked"},
                     headers=_auth(hospital_b["token"]))
    assert r.status_code == 404, r.text

    # Hospital B tries to DELETE hospital A's personnel → 404
    r = client.delete(f"/personnel/{personnel_id}",
                      headers=_auth(hospital_b["token"]))
    assert r.status_code == 404, r.text


# ── Test 4: Personnel hard-delete removes the row entirely ────────────────────

def test_04_personnel_hard_delete(client, hospital_a):
    hosp_id = hospital_a["id"]
    token = hospital_a["token"]

    # Add a personnel specifically to delete
    r = client.post(f"/hospitals/{hosp_id}/personnel",
                    json={"name": "To Be Deleted", "phone": f"2372{uid()}", "role": "nurse"},
                    headers=_auth(token))
    assert r.status_code == 201
    personnel_id = r.json()["id"]

    # Hard-delete
    r = client.delete(f"/personnel/{personnel_id}", headers=_auth(token))
    assert r.status_code == 204

    # Verify the row is GONE from the DB (not just flagged)
    db = SessionLocal()
    try:
        from app.models.personnel import Personnel
        row = db.query(Personnel).filter(Personnel.id == personnel_id).first()
        assert row is None, "Row should have been hard-deleted"
    finally:
        db.close()


# ── Test 5: Patient self-access only ─────────────────────────────────────────

def test_05_patient_self_access(client, patient_a, patient_b):
    # Patient A can read own record
    r = client.get(f"/patients/{patient_a['id']}", headers=_auth(patient_a["token"]))
    assert r.status_code == 200

    # Patient A cannot read Patient B's record → 403
    r = client.get(f"/patients/{patient_b['id']}", headers=_auth(patient_a["token"]))
    assert r.status_code == 403

    # Patient A can PATCH own record
    r = client.patch(f"/patients/{patient_a['id']}",
                     json={"language": "fr"},
                     headers=_auth(patient_a["token"]))
    assert r.status_code == 200
    assert r.json()["language"] == "fr"

    # Patient A cannot PATCH Patient B's record → 403
    r = client.patch(f"/patients/{patient_b['id']}",
                     json={"language": "en"},
                     headers=_auth(patient_a["token"]))
    assert r.status_code == 403


# ── Test 6: Patient calling GET /patients → 403 ───────────────────────────────

def test_06_patient_cannot_list_patients(client, patient_a):
    r = client.get("/patients", headers=_auth(patient_a["token"]))
    assert r.status_code == 403


# ── Test 7: Hospital sees only its own patients ───────────────────────────────

def test_07_hospital_patient_scoping(client, hospital_a, hospital_b,
                                     patient_a, patient_b):
    # Hospital A list
    r_a = client.get("/patients", headers=_auth(hospital_a["token"]))
    assert r_a.status_code == 200
    ids_a = {p["id"] for p in r_a.json()}

    # Hospital B list
    r_b = client.get("/patients", headers=_auth(hospital_b["token"]))
    assert r_b.status_code == 200
    ids_b = {p["id"] for p in r_b.json()}

    # Each hospital's patient is in its own list, not the other
    assert patient_a["id"] in ids_a
    assert patient_b["id"] not in ids_a
    assert patient_b["id"] in ids_b
    assert patient_a["id"] not in ids_b

    # Lists must not overlap
    assert ids_a.isdisjoint(ids_b)


# ── Test 8: Hospital requesting another hospital's patient by id → 404 ────────

def test_08_cross_hospital_patient_get_returns_404(client, hospital_a, patient_b):
    # hospital_a tries to read hospital_b's patient → 404 (not 403)
    r = client.get(f"/patients/{patient_b['id']}", headers=_auth(hospital_a["token"]))
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"


# ── Test 9: Cross-hospital patient PATCH/DELETE → 404 ────────────────────────

def test_09_cross_hospital_patient_edit_blocked(client, hospital_a, patient_b):
    r = client.patch(f"/patients/{patient_b['id']}",
                     json={"language": "en"},
                     headers=_auth(hospital_a["token"]))
    assert r.status_code == 404

    r = client.delete(f"/patients/{patient_b['id']}",
                      headers=_auth(hospital_a["token"]))
    assert r.status_code == 404


# ── Test 10: Patient soft-delete ──────────────────────────────────────────────

def test_10_patient_soft_delete(client, hospital_a):
    # Create a dedicated patient to delete
    doomed = _signup_patient(client, hospital_a["id"], "Doomed")
    patient_id = doomed["id"]

    # Patient self-deletes
    r = client.delete(f"/patients/{patient_id}", headers=_auth(doomed["token"]))
    assert r.status_code == 204

    # is_active is False in the DB
    db = SessionLocal()
    try:
        from app.models.patient import Patient
        row = db.query(Patient).filter(Patient.id == patient_id).first()
        assert row is not None, "Row must still exist (soft delete)"
        assert row.is_active is False
    finally:
        db.close()

    # Absent from hospital's GET /patients list
    r = client.get("/patients", headers=_auth(hospital_a["token"]))
    ids = {p["id"] for p in r.json()}
    assert patient_id not in ids

    # GET /patients/{id} returns 404 for the now-inactive patient
    r = client.get(f"/patients/{patient_id}", headers=_auth(doomed["token"]))
    assert r.status_code == 404


# ── Test 11: Hospital soft-delete ─────────────────────────────────────────────

def test_11_hospital_soft_delete(client):
    # Create a dedicated hospital to delete
    doomed_hosp = _signup_hospital(client, "Doomed")
    hosp_id = doomed_hosp["id"]
    token = doomed_hosp["token"]

    # Soft-delete
    r = client.delete(f"/hospitals/{hosp_id}", headers=_auth(token))
    assert r.status_code == 204

    # Row still exists in DB but is_active=False
    db = SessionLocal()
    try:
        from app.models.hospital import Hospital
        row = db.query(Hospital).filter(Hospital.id == hosp_id).first()
        assert row is not None, "Row must still exist (soft delete)"
        assert row.is_active is False
    finally:
        db.close()

    # Hospital absent from public GET /hospitals list
    r = client.get("/hospitals")
    ids = {h["id"] for h in r.json()}
    assert hosp_id not in ids


# ── Test 12: Patient cannot reach hospital or personnel endpoints ──────────────

def test_12_patient_blocked_from_hospital_personnel_endpoints(
        client, patient_a, hospital_a):
    hosp_id = hospital_a["id"]
    tok = patient_a["token"]

    # Cannot GET a specific hospital (hospital-only self endpoint)
    r = client.get(f"/hospitals/{hosp_id}", headers=_auth(tok))
    assert r.status_code == 403

    # Cannot PATCH hospital
    r = client.patch(f"/hospitals/{hosp_id}", json={"name": "Hacked"},
                     headers=_auth(tok))
    assert r.status_code == 403

    # Cannot DELETE hospital
    r = client.delete(f"/hospitals/{hosp_id}", headers=_auth(tok))
    assert r.status_code == 403

    # Cannot add personnel
    r = client.post(f"/hospitals/{hosp_id}/personnel",
                    json={"name": "X", "phone": "237000", "role": "nurse"},
                    headers=_auth(tok))
    assert r.status_code == 403

    # Cannot list personnel
    r = client.get(f"/hospitals/{hosp_id}/personnel", headers=_auth(tok))
    assert r.status_code == 403
