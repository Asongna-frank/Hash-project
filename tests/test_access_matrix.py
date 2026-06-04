# tests/test_access_matrix.py
"""
Access-control matrix + hospital-side patient creation + audit logging.

Runs against the live DB via TestClient (external services mocked). Each test
is self-cleaning via the module fixtures, which delete every created row at teardown.

Run:  pytest tests/test_access_matrix.py -v
"""

import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# Mock externals before importing the app
from app.services import sms_service as sms_mod, push_service as push_mod
from app.services.sms_service import SMSResult
from app.services.push_service import PushResult
sms_mod.sms_service.send_sms = lambda to, message: SMSResult(ok=True, provider_message_id="T")
push_mod.push_service.send_push = lambda patient_uuid, title, message: PushResult(ok=True, recipients=0)

from app.main import app
from app.core.database import SessionLocal
from app.models.audit_log import AuditLog

PW = "StrongPass-123!"
_pn = itertools.count(1)


def phone():
    return "+237677" + f"{next(_pn):06d}"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def audit_count(action, target_id):
    db = SessionLocal()
    try:
        return db.query(AuditLog).filter(
            AuditLog.action == action, AuditLog.target_id == str(target_id)
        ).count()
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def world(client):
    """Two hospitals + a patient under each; cleaned up at teardown."""
    created = {"hospitals": [], "patients": []}

    def signup_hospital():
        hp = phone()
        r = client.post("/auth/hospital/signup", json={
            "name": "H", "phone": hp, "password": PW, "address": "X",
            "first_personnel": {"name": "p", "phone": phone(), "role": "admin"}})
        hid = r.json()["id"]
        created["hospitals"].append(hid)
        tok = client.post("/auth/hospital/login", json={"phone": hp, "password": PW}).json()["access_token"]
        return hid, tok

    def signup_patient(hid):
        pp = phone()
        r = client.post("/auth/patient/signup", json={
            "name": "Pat", "phone": pp, "password": PW, "hospital_id": hid,
            "weeks_pregnant_at_signup": 20, "age": 28})
        pid = r.json()["id"]
        created["patients"].append(pid)
        tok = client.post("/auth/patient/login", json={"phone": pp, "password": PW}).json()["access_token"]
        return pid, tok, pp

    hA, tokHA = signup_hospital()
    hB, tokHB = signup_hospital()
    pA, tokPA, phoneA = signup_patient(hA)
    pB, tokPB, phoneB = signup_patient(hB)

    yield dict(hA=hA, tokHA=tokHA, hB=hB, tokHB=tokHB,
               pA=pA, tokPA=tokPA, phoneA=phoneA,
               pB=pB, tokPB=tokPB, phoneB=phoneB, created=created)

    # teardown — delete everything created in this module
    db = SessionLocal()
    try:
        for pid in created["patients"]:
            for t in ["appointments", "messages", "risk_assessments", "pregnancies"]:
                db.execute(text(f"DELETE FROM {t} WHERE patient_id = :p"), {"p": pid})
        db.execute(text("DELETE FROM audit_logs WHERE target_id = ANY(:ids)"),
                   {"ids": [str(x) for x in created["patients"]]})
        for hid in created["hospitals"]:
            db.execute(text("DELETE FROM patients WHERE hospital_id = :h"), {"h": hid})
            db.execute(text("DELETE FROM personnel WHERE hospital_id = :h"), {"h": hid})
            db.execute(text("DELETE FROM hospitals WHERE id = :h"), {"h": hid})
        db.commit()
    finally:
        db.close()


# 1. Patient → GET /patients (list) → 403
def test_patient_cannot_list_patients(client, world):
    assert client.get("/patients", headers=auth(world["tokPA"])).status_code == 403


# 2. Patient → hospital/personnel endpoints → 403
def test_patient_cannot_reach_hospital_endpoints(client, world):
    assert client.get(f"/hospitals/{world['hA']}", headers=auth(world["tokPA"])).status_code == 403
    assert client.get(f"/hospitals/{world['hA']}/personnel",
                      headers=auth(world["tokPA"])).status_code == 403


# 3. Patient → another patient's /patients/{id} → 404 (not 403)
def test_patient_other_patient_404(client, world):
    assert client.get(f"/patients/{world['pB']}", headers=auth(world["tokPA"])).status_code == 404


# 4. Hospital A → hospital B's patient (GET/PATCH/DELETE) → 404
def test_cross_hospital_patient_404(client, world):
    assert client.get(f"/patients/{world['pB']}", headers=auth(world["tokHA"])).status_code == 404
    assert client.patch(f"/patients/{world['pB']}", headers=auth(world["tokHA"]),
                        json={"name": "x"}).status_code == 404
    assert client.delete(f"/patients/{world['pB']}", headers=auth(world["tokHA"])).status_code == 404


# 5. Hospital → its own patient: GET/PATCH work (DELETE covered separately to keep pA alive)
def test_hospital_own_patient_ok(client, world):
    assert client.get(f"/patients/{world['pA']}", headers=auth(world["tokHA"])).status_code == 200
    r = client.patch(f"/patients/{world['pA']}", headers=auth(world["tokHA"]),
                     json={"language": "fr"})
    assert r.status_code == 200 and r.json()["language"] == "fr"


# 6. Public self-signup → smartphone + has password + no personnel attribution
def test_self_signup_is_smartphone_with_password(client, world):
    r = client.get(f"/patients/{world['pA']}", headers=auth(world["tokPA"]))
    assert r.json()["account_type"] == "smartphone"
    db = SessionLocal()
    try:
        from app.models.patient import Patient
        p = db.query(Patient).filter(Patient.id == world["pA"]).first()
        assert p.hashed_password is not None
    finally:
        db.close()
    # self-signup wrote an audit row, with no personnel attribution
    assert audit_count("patient.signup", world["pA"]) == 1


# 7. POST /hospital/patients → choronko, no password, hospital from token, no personnel attribution
def test_hospital_creates_choronko_patient(client, world):
    ph = phone()
    r = client.post("/hospital/patients", headers=auth(world["tokHA"]), json={
        "name": "Choronko Pat", "phone": ph, "weeks_pregnant_at_signup": 18, "age": 34})
    assert r.status_code == 201, r.text
    body = r.json()
    world["created"]["patients"].append(body["id"])
    assert body["account_type"] == "choronko"
    assert body["hospital_id"] == world["hA"]  # from token
    db = SessionLocal()
    try:
        from app.models.patient import Patient
        p = db.query(Patient).filter(Patient.id == body["id"]).first()
        assert p.hashed_password is None
    finally:
        db.close()
    rows = audit_count("patient.create", body["id"])
    assert rows == 1
    # no personnel attribution in the audit details
    db = SessionLocal()
    try:
        a = db.query(AuditLog).filter(AuditLog.action == "patient.create",
                                      AuditLog.target_id == str(body["id"])).first()
        assert "personnel" not in (a.details or {})
        assert "created_by_personnel_id" not in (a.details or {})
    finally:
        db.close()


# 8. hospital_id in body is ignored/overridden by token
def test_hospital_create_ignores_body_hospital_id(client, world):
    ph = phone()
    r = client.post("/hospital/patients", headers=auth(world["tokHA"]), json={
        "name": "Spoof", "phone": ph, "weeks_pregnant_at_signup": 12, "age": 25,
        "hospital_id": world["hB"]})  # attempt to plant under hospital B
    assert r.status_code == 201, r.text
    world["created"]["patients"].append(r.json()["id"])
    assert r.json()["hospital_id"] == world["hA"]  # token wins


# 9. Patient → POST /hospital/patients → 403
def test_patient_cannot_create_hospital_patient(client, world):
    r = client.post("/hospital/patients", headers=auth(world["tokPA"]), json={
        "name": "x", "phone": phone(), "weeks_pregnant_at_signup": 12, "age": 25})
    assert r.status_code == 403


# 10. Hospital edits a patient's phone → normalized, uniqueness enforced, audited
def test_hospital_edits_phone_with_uniqueness_and_audit(client, world):
    # collision: try to set pA's phone to pB's phone → 409
    clash = client.patch(f"/patients/{world['pA']}", headers=auth(world["tokHA"]),
                         json={"phone": world["phoneB"]})
    assert clash.status_code == 409
    # valid change in local format → normalized to E.164 and audited
    new_local = phone().replace("+237", "")
    r = client.patch(f"/patients/{world['pA']}", headers=auth(world["tokHA"]),
                     json={"phone": new_local})
    assert r.status_code == 200
    assert r.json()["phone"] == "+237" + new_local
    db = SessionLocal()
    try:
        a = (db.query(AuditLog)
             .filter(AuditLog.action == "patient.update", AuditLog.target_id == str(world["pA"]))
             .order_by(AuditLog.created_at.desc()).first())
        assert a is not None and a.details.get("phone_changed") is True
    finally:
        db.close()


# 11. Every create/edit/delete writes an audit row (delete a throwaway patient)
def test_state_changes_are_audited(client, world):
    ph = phone()
    r = client.post("/hospital/patients", headers=auth(world["tokHA"]), json={
        "name": "Temp", "phone": ph, "weeks_pregnant_at_signup": 22, "age": 31})
    pid = r.json()["id"]
    world["created"]["patients"].append(pid)
    assert audit_count("patient.create", pid) == 1          # create
    client.patch(f"/patients/{pid}", headers=auth(world["tokHA"]), json={"language": "en"})
    assert audit_count("patient.update", pid) >= 1          # edit
    assert client.delete(f"/patients/{pid}", headers=auth(world["tokHA"])).status_code == 204
    assert audit_count("patient.delete", pid) == 1          # delete


# 12. Risk override is hospital-only and audited
def test_risk_override_audited_and_hospital_only(client, world):
    assert client.patch(f"/patients/{world['pA']}/risk-level", headers=auth(world["tokPA"]),
                        json={"new_level": "low", "reason": "x"}).status_code == 403
    r = client.patch(f"/patients/{world['pA']}/risk-level", headers=auth(world["tokHA"]),
                     json={"new_level": "high", "reason": "clinical"})
    assert r.status_code == 200
    assert audit_count("patient.risk_override", world["pA"]) == 1
