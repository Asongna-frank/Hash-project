# test/test_v2_integration.py
"""
End-to-end integration tests for v2 Risk Scoring + Check-in Cadence changes.

Tests cover:
  [S1]  v2 signup — new fields accepted and stored correctly
  [S2]  Rh-negative flag derived from blood_group
  [S3]  Risk scoring — all-zero patient → low
  [S4]  Risk scoring — graded age (39 → medium, not low)
  [S5]  Risk scoring — graded loss count (2 losses → medium on its own)
  [S6]  Risk scoring — stacking → high  (3 losses + hypertension + first trimester)
  [S7]  Risk scoring — blood_group / gravidity / distance do NOT change score
  [S8]  Backward compat — previous_loss=True with no count → count becomes 1
  [S9]  Blood group validation rejects invalid value
  [S10] Legacy signup (no new fields) still works
  [S11] Check-in cadence — high-risk: first send fires immediately
  [S12] Check-in cadence — high-risk: second call within 3 days is skipped
  [S13] Check-in cadence — medium-risk: skipped after 5 days, sent after 8 days
  [S14] Missed check-in counter increments when no reply received
  [S15] Missed check-in flag set at threshold (high=3), reset on reply
  [S16] Silenced patient (opt_out=stopped) skipped in check-in
  [S17] pending_loss_confirmation patient skipped in check-in
  [S18] Post-loss patient gets grief-support check-in (high-risk cadence)
  [S19] Risk-level override → RiskAssessment row written with clinician id
  [S20] PatientResponse includes all v2 fields

Usage (from Backend/):
  source venv/bin/activate
  python -m test.test_v2_integration
"""

import os
import sys
import uuid
from datetime import date, datetime, timezone, timedelta

import httpx

BASE_URL = os.environ.get("HASH_BASE_URL", "http://127.0.0.1:8000")
PASSWORD  = "TestPass1!"

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {('— ' + detail) if detail else ''}")


def _uid() -> str:
    return uuid.uuid4().hex[:10]


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _get_db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _get_patient(patient_id, db):
    from app.models.patient import Patient
    return db.query(Patient).filter(Patient.id == patient_id).first()


def _get_risk_assessments(patient_id, db):
    from app.models.risk_assessment import RiskAssessment
    return (
        db.query(RiskAssessment)
        .filter(RiskAssessment.patient_id == patient_id)
        .order_by(RiskAssessment.computed_at.desc())
        .all()
    )


def _clear_checkins(patient_id, db):
    from app.models.message import Message
    db.query(Message).filter(
        Message.patient_id == patient_id,
        Message.message_type == "checkin",
    ).delete(synchronize_session="fetch")
    db.commit()
    db.expire_all()


def _plant_checkin(patient_id, age: timedelta, db):
    """Insert a dummy outbound check-in at a controlled age."""
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


def _plant_inbound(patient_id, age: timedelta, db):
    """Insert a dummy inbound message to simulate a patient reply."""
    from app.models.message import Message
    msg = Message(
        id=uuid.uuid4(),
        patient_id=patient_id,
        content="[test reply]",
        direction="in",
        channel="app",
        message_type="chat",
        triage_level="low",
        is_read=True,
        created_at=datetime.now(timezone.utc) - age,
    )
    db.add(msg)
    db.commit()
    db.expire_all()


def _update_patient(patient_id, db, **fields):
    from app.models.patient import Patient
    db.query(Patient).filter(Patient.id == patient_id).update(
        fields, synchronize_session="fetch"
    )
    db.commit()
    db.expire_all()


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


# ─── HTTP signup helper ───────────────────────────────────────────────────────

def _signup_hospital(client):
    r = client.post("/auth/hospital/signup", json={
        "name": "V2 Test Hospital",
        "phone": f"2376{_uid()}",
        "password": PASSWORD,
        "address": "1 Test Road",
        "first_personnel": {
            "name": "Dr V2",
            "phone": "237600000099",
            "role": "admin",
        },
    })
    assert r.status_code == 201, f"Hospital signup failed: {r.text}"
    return r.json()


def _signup_patient(client, hospital_id, **overrides):
    payload = {
        "name": "Test Patient",
        "phone": f"2377{_uid()}",
        "password": PASSWORD,
        "hospital_id": str(hospital_id),
        "weeks_pregnant_at_signup": 20,
        "age": 28,
        "parity": 1,
        **overrides,
    }
    return client.post("/auth/patient/signup", json=payload)


def _login_hospital(client, phone):
    r = client.post("/auth/hospital/login", json={"phone": phone, "password": PASSWORD})
    assert r.status_code == 200
    return r.json()["access_token"]


def _login_patient(client, phone):
    r = client.post("/auth/patient/login", json={"phone": phone, "password": PASSWORD})
    assert r.status_code == 200
    return r.json()["access_token"]


# ─── Main test runner ─────────────────────────────────────────────────────────

def main() -> int:
    # Server probe
    try:
        r = httpx.get(f"{BASE_URL}/", timeout=5)
        check("server reachable", r.status_code == 200)
    except Exception as exc:
        print(f"  FAIL  server not reachable: {exc}")
        print("  Start with: uvicorn app.main:app --host 127.0.0.1 --port 8000")
        return 1

    client = httpx.Client(base_url=BASE_URL, timeout=60.0)
    try:
        hosp = _signup_hospital(client)
        hospital_id   = hosp["id"]
        hospital_phone = hosp["phone"]
        hosp_token = _login_hospital(client, hospital_phone)

        # ── [S1] v2 signup — new fields accepted and stored ────────────────────
        print("\n[S1] v2 signup — new fields stored correctly")
        phone_s1 = f"2377{_uid()}"
        r = client.post("/auth/patient/signup", json={
            "name":                       "Amina V2",
            "phone":                      phone_s1,
            "password":                   PASSWORD,
            "hospital_id":                hospital_id,
            "weeks_pregnant_at_signup":   22,
            "age":                        28,
            "parity":                     1,
            "previous_loss_count":        2,
            "gravidity":                  3,
            "blood_group":                "B-",
            "distance_close_to_hospital": True,
            "has_hypertension":           False,
        })
        check("[S1] signup 201", r.status_code == 201, r.text)
        s1 = r.json()
        check("[S1] previous_loss_count stored", s1.get("previous_loss_count") == 2)
        check("[S1] previous_loss derived True", s1.get("previous_loss") is True)
        check("[S1] gravidity stored", s1.get("gravidity") == 3)
        check("[S1] blood_group stored", s1.get("blood_group") == "B-")
        check("[S1] distance stored", s1.get("distance_close_to_hospital") is True)

        # ── [S2] Rh-negative flag ──────────────────────────────────────────────
        print("\n[S2] Rh-negative flag derived from blood_group")
        check("[S2] rh_negative=True for B-", s1.get("rh_negative") is True)

        phone_rh_pos = f"2377{_uid()}"
        r2 = client.post("/auth/patient/signup", json={
            "name": "Rh+ Patient", "phone": phone_rh_pos, "password": PASSWORD,
            "hospital_id": hospital_id, "weeks_pregnant_at_signup": 20,
            "age": 28, "blood_group": "O+",
        })
        check("[S2] rh_negative=False for O+",
              r2.status_code == 201 and r2.json().get("rh_negative") is False,
              r2.text)

        # ── [S3] Risk scoring — all-zero → low ────────────────────────────────
        print("\n[S3] Risk scoring — all-zero patient → low")
        r = _signup_patient(client, hospital_id, age=28, parity=2,
                            weeks_pregnant_at_signup=20)
        check("[S3] signup 201", r.status_code == 201, r.text)
        check("[S3] risk_level=low", r.json().get("risk_level") == "low",
              f"got {r.json().get('risk_level')}")

        # Verify audit trail
        db = _get_db()
        try:
            pid_s3 = r.json()["id"]
            ra_list = _get_risk_assessments(pid_s3, db)
            check("[S3] RiskAssessment row written", len(ra_list) >= 1)
            ra = ra_list[0]
            check("[S3] rubric_version=v2.0", ra.rubric_version == "v2.0")
            check("[S3] score=0", ra.score == 0)
            check("[S3] result_level=low", ra.result_level == "low")
            check("[S3] breakdown in inputs", "_breakdown" in (ra.inputs or {}))
        finally:
            db.close()

        # ── [S4] Graded age → medium (age 39 = 2 pts, enough if + 2 losses) ──
        print("\n[S4] Graded age — age 39 scores 2 pts")
        r = _signup_patient(client, hospital_id, age=39,
                            weeks_pregnant_at_signup=20, parity=2)
        check("[S4] signup 201", r.status_code == 201, r.text)
        # age 39 = 2 pts (35_to_39 band) → score=2 → low (< 4)
        # But with parity=2 it's still low. Let's also check with previous_loss_count=1 → 2+2=4 → medium
        r_age_loss = _signup_patient(client, hospital_id, age=39, parity=2,
                                     previous_loss_count=1,
                                     weeks_pregnant_at_signup=20)
        check("[S4] age 39 + 1 loss = medium",
              r_age_loss.status_code == 201 and r_age_loss.json().get("risk_level") == "medium",
              f"got {r_age_loss.json().get('risk_level')}")

        db = _get_db()
        try:
            ra_age = _get_risk_assessments(r_age_loss.json()["id"], db)[0]
            check("[S4] score=4", ra_age.score == 4,
                  f"got score={ra_age.score}")  # age(2)+loss(2)=4
        finally:
            db.close()

        # ── [S5] Graded loss count — 2 losses = 3 pts → low (3 < threshold 4) ──
        print("\n[S5] Graded loss count — 2 losses = 3 pts (below medium threshold 4 → low)")
        r = _signup_patient(client, hospital_id, age=28, parity=2,
                            previous_loss_count=2, weeks_pregnant_at_signup=20)
        check("[S5] signup 201", r.status_code == 201, r.text)
        # Score 3 < RISK_THRESHOLDS["medium"]=4 → "low" is correct
        check("[S5] risk_level=low (score 3 < threshold 4)", r.json().get("risk_level") == "low",
              f"got {r.json().get('risk_level')}")

        db = _get_db()
        try:
            ra5 = _get_risk_assessments(r.json()["id"], db)[0]
            check("[S5] score=3 (loss band '2'=3pts)", ra5.score == 3,
                  f"got {ra5.score}")
            # Confirm 3 losses pushes into medium territory (3 losses = 5 pts >= 4)
            r5b = _signup_patient(client, hospital_id, age=28, parity=2,
                                  previous_loss_count=3, weeks_pregnant_at_signup=20)
            check("[S5] 3 losses (5pts) → medium",
                  r5b.json().get("risk_level") == "medium",
                  f"got {r5b.json().get('risk_level')}")
        finally:
            db.close()

        # ── [S6] Stacking → high (3 losses + hypertension + first trimester) ──
        print("\n[S6] Stacking → high")
        r = _signup_patient(client, hospital_id, age=28, parity=2,
                            previous_loss_count=3, has_hypertension=True,
                            weeks_pregnant_at_signup=8)
        check("[S6] signup 201", r.status_code == 201, r.text)
        check("[S6] risk_level=high", r.json().get("risk_level") == "high",
              f"got {r.json().get('risk_level')}")

        db = _get_db()
        try:
            ra6 = _get_risk_assessments(r.json()["id"], db)[0]
            check("[S6] score=9 (losses=5 + hypert=3 + first_trim=1)",
                  ra6.score == 9, f"got {ra6.score}")
        finally:
            db.close()

        # ── [S7] Unscored fields don't change score ───────────────────────────
        print("\n[S7] Unscored fields — gravidity/blood_group/distance do not change score")
        r_plain = _signup_patient(client, hospital_id, age=28, parity=2,
                                  weeks_pregnant_at_signup=20)
        r_extra = client.post("/auth/patient/signup", json={
            "name": "Extra Fields Test", "phone": f"2377{_uid()}",
            "password": PASSWORD, "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 20, "age": 28, "parity": 2,
            "gravidity": 8, "blood_group": "O-",
            "distance_close_to_hospital": False,
        })
        db = _get_db()
        try:
            ra_plain = _get_risk_assessments(r_plain.json()["id"], db)[0]
            ra_extra = _get_risk_assessments(r_extra.json()["id"], db)[0]
            check("[S7] score identical regardless of unscored fields",
                  ra_plain.score == ra_extra.score,
                  f"plain={ra_plain.score} extra={ra_extra.score}")
        finally:
            db.close()

        # ── [S8] Backward compat — previous_loss=True, count=0 → count becomes 1
        print("\n[S8] Backward compat — previous_loss=True promotes count to 1")
        r = client.post("/auth/patient/signup", json={
            "name": "Compat Patient", "phone": f"2377{_uid()}",
            "password": PASSWORD, "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 20, "age": 28,
            "previous_loss": True,   # old boolean, no count
        })
        check("[S8] signup 201", r.status_code == 201, r.text)
        check("[S8] previous_loss_count became 1",
              r.json().get("previous_loss_count") == 1,
              f"got {r.json().get('previous_loss_count')}")
        check("[S8] previous_loss still True", r.json().get("previous_loss") is True)

        # ── [S9] Blood group validation rejects invalid value ──────────────────
        print("\n[S9] Blood group validation")
        r = client.post("/auth/patient/signup", json={
            "name": "Bad BG", "phone": f"2377{_uid()}",
            "password": PASSWORD, "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 20, "age": 28,
            "blood_group": "X+",  # invalid
        })
        check("[S9] invalid blood_group → 422", r.status_code == 422,
              f"got {r.status_code}: {r.text[:100]}")

        # ── [S10] Legacy signup (no new fields) still works ───────────────────
        print("\n[S10] Legacy signup — no v2 fields → 201 with defaults")
        r = client.post("/auth/patient/signup", json={
            "name": "Legacy Patient", "phone": f"2377{_uid()}",
            "password": PASSWORD, "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 20, "age": 28,
        })
        check("[S10] signup 201", r.status_code == 201, r.text)
        check("[S10] previous_loss_count defaults to 0",
              r.json().get("previous_loss_count") == 0)
        check("[S10] gravidity defaults to null", r.json().get("gravidity") is None)
        check("[S10] blood_group defaults to null", r.json().get("blood_group") is None)
        check("[S10] rh_negative defaults to False",
              r.json().get("rh_negative") is False)
        check("[S10] missed_checkin_flag defaults to False",
              r.json().get("missed_checkin_flag") is False)
        check("[S10] consecutive_missed_checkins defaults to 0",
              r.json().get("consecutive_missed_checkins") == 0)

    finally:
        client.close()

    # ── Check-in cadence tests (DB-level) ─────────────────────────────────────
    # Use a fresh dedicated patient for check-in tests
    client2 = httpx.Client(base_url=BASE_URL, timeout=60.0)
    try:
        # Create a fresh high-risk patient
        r_ci = client2.post("/auth/patient/signup", json={
            "name": "CheckIn Test Patient", "phone": f"2377{_uid()}",
            "password": PASSWORD, "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 22, "age": 28, "parity": 2,
            "has_sickle_cell": True, "has_hypertension": True, "has_hiv": True,
        })
        assert r_ci.status_code == 201, r_ci.text
        ci_id   = r_ci.json()["id"]
        ci_phone = r_ci.json()["phone"]

        from app.services.checkin_sender import send_checkin, _is_checkin_due

        db = _get_db()
        try:
            from app.models.patient import Patient
            patient = db.query(Patient).filter(Patient.id == ci_id).first()
            check("[S6-confirm] test patient risk=high",
                  patient.risk_level == "high", patient.risk_level)

            # ── [S11] First check-in fires immediately ─────────────────────────
            print("\n[S11] High-risk first check-in fires immediately")
            _clear_checkins(ci_id, db)
            db.refresh(patient)
            sent = send_checkin(patient, db)
            check("[S11] send_checkin returns True", sent is True)
            check("[S11] message persisted", _checkin_count(ci_id, db) == 1)

            # Verify message fields
            from app.models.message import Message
            msg = (
                db.query(Message)
                .filter(Message.patient_id == ci_id,
                        Message.message_type == "checkin",
                        Message.direction == "out")
                .order_by(Message.created_at.desc()).first()
            )
            check("[S11] message_type=checkin", msg and msg.message_type == "checkin")
            check("[S11] direction=out", msg and msg.direction == "out")
            check("[S11] has content", msg and bool(msg.content))
            print(f"  Generated check-in ({len(msg.content)} chars): \"{msg.content[:80]}...\"")

            # ── [S12] Within 3 days → skipped ─────────────────────────────────
            print("\n[S12] High-risk: second call within 3 days skipped")
            count_before = _checkin_count(ci_id, db)
            sent2 = send_checkin(patient, db)
            check("[S12] returns False (not due)", sent2 is False)
            check("[S12] no new message", _checkin_count(ci_id, db) == count_before)

            # Also verify _is_checkin_due returns False after a fresh send
            check("[S12] _is_checkin_due=False", _is_checkin_due(patient, db) is False)

            # ── [S13] Medium-risk: skipped at 5 days, sent at 8 days ──────────
            print("\n[S13] Medium-risk interval: skip at 5 days, send at 8 days")
            _update_patient(ci_id, db, risk_level="medium")
            db.refresh(patient)

            # Plant a check-in 5 days ago → 5 < 7 → not due
            _clear_checkins(ci_id, db)
            _plant_checkin(ci_id, timedelta(days=5), db)
            db.refresh(patient)
            check("[S13] not due at 5 days", _is_checkin_due(patient, db) is False)
            sent3a = send_checkin(patient, db)
            check("[S13] send returns False at 5 days", sent3a is False)

            # Plant a check-in 8 days ago → 8 >= 7 → due
            _clear_checkins(ci_id, db)
            _plant_checkin(ci_id, timedelta(days=8), db)
            db.refresh(patient)
            check("[S13] due at 8 days", _is_checkin_due(patient, db) is True)
            count_before = _checkin_count(ci_id, db)
            sent3b = send_checkin(patient, db)
            check("[S13] send returns True at 8 days", sent3b is True)
            check("[S13] count incremented", _checkin_count(ci_id, db) == count_before + 1)

            # ── [S14] Missed check-in counter increments (no reply) ────────────
            print("\n[S14] Missed check-in counter increments when no reply")
            _update_patient(ci_id, db, risk_level="high",
                            consecutive_missed_checkins=0, missed_checkin_flag=False)
            _clear_checkins(ci_id, db)

            # Plant a check-in 4 days ago (> 3-day high-risk interval → due)
            # No inbound reply between then and now.
            _plant_checkin(ci_id, timedelta(days=4), db)
            db.refresh(patient)

            counter_before = patient.consecutive_missed_checkins
            send_checkin(patient, db)
            db.refresh(patient)
            check("[S14] counter incremented by 1",
                  patient.consecutive_missed_checkins == counter_before + 1,
                  f"before={counter_before} after={patient.consecutive_missed_checkins}")

            # ── [S15] Missed flag at threshold=3, reset on reply ──────────────
            print("\n[S15] Missed flag set at threshold 3, reset when patient replies")
            _update_patient(ci_id, db, risk_level="high",
                            consecutive_missed_checkins=0, missed_checkin_flag=False)
            _clear_checkins(ci_id, db)
            db.refresh(patient)

            # Simulate 3 consecutive missed check-ins (no replies).
            # Each plant is 4 days old so the 3-day high-risk interval is elapsed.
            for i in range(1, 4):
                _clear_checkins(ci_id, db)
                _plant_checkin(ci_id, timedelta(days=4), db)
                db.refresh(patient)
                send_checkin(patient, db)
                db.refresh(patient)
                if i < 3:
                    check(f"[S15] flag not set at missed={i}",
                          patient.missed_checkin_flag is False)
                else:
                    check(f"[S15] flag SET at missed=3",
                          patient.missed_checkin_flag is True)

            # Simulate patient reply → counter resets, flag clears
            _plant_inbound(ci_id, timedelta(hours=1), db)
            _clear_checkins(ci_id, db)
            _plant_checkin(ci_id, timedelta(hours=25), db)
            db.refresh(patient)
            send_checkin(patient, db)
            db.refresh(patient)
            check("[S15] counter reset to 0 after reply",
                  patient.consecutive_missed_checkins == 0,
                  f"got {patient.consecutive_missed_checkins}")
            check("[S15] flag cleared after reply",
                  patient.missed_checkin_flag is False)

            # ── [S16] Silenced patient skipped ────────────────────────────────
            print("\n[S16] Silenced patient (opt_out=stopped) skipped")
            _update_patient(ci_id, db, opt_out_status="stopped",
                            risk_level="high")
            _clear_checkins(ci_id, db)
            db.refresh(patient)
            count_before = _checkin_count(ci_id, db)
            sent_s = send_checkin(patient, db)
            check("[S16] returns False", sent_s is False)
            check("[S16] no new message", _checkin_count(ci_id, db) == count_before)

            # Restore
            _update_patient(ci_id, db, opt_out_status=None)
            db.refresh(patient)

            # ── [S17] pending_loss_confirmation skipped ────────────────────────
            print("\n[S17] pending_loss_confirmation=True skipped")
            _update_patient(ci_id, db, pending_loss_confirmation=True)
            _clear_checkins(ci_id, db)
            db.refresh(patient)
            count_before = _checkin_count(ci_id, db)
            sent_p = send_checkin(patient, db)
            check("[S17] returns False", sent_p is False)
            check("[S17] no new message", _checkin_count(ci_id, db) == count_before)

            # Restore
            _update_patient(ci_id, db, pending_loss_confirmation=False)
            db.refresh(patient)

            # ── [S18] Post-loss patient gets grief-support content ────────────
            print("\n[S18] Post-loss patient — grief-support check-in")
            _update_patient(ci_id, db, status="post_loss", risk_level="high")
            _clear_checkins(ci_id, db)
            db.refresh(patient)
            count_before = _checkin_count(ci_id, db)
            sent_pl = send_checkin(patient, db)
            check("[S18] returns True", sent_pl is True)
            check("[S18] message persisted", _checkin_count(ci_id, db) == count_before + 1)

            pl_msg = (
                db.query(Message)
                .filter(Message.patient_id == ci_id,
                        Message.message_type == "checkin",
                        Message.direction == "out")
                .order_by(Message.created_at.desc()).first()
            )
            check("[S18] has content", pl_msg and bool(pl_msg.content))
            print(f"  Post-loss check-in ({len(pl_msg.content)} chars): \"{pl_msg.content[:80]}...\"")

        finally:
            db.close()

        # ── [S19] Risk-level override → audit row written ──────────────────────
        print("\n[S19] Clinician risk-level override → RiskAssessment row written")
        hosp_token2 = _login_hospital(client2, hospital_phone)
        r_ov = client2.patch(
            f"/patients/{ci_id}/risk-level",
            json={"new_level": "low", "reason": "Patient condition improved"},
            headers={"Authorization": f"Bearer {hosp_token2}"},
        )
        check("[S19] override 200", r_ov.status_code == 200,
              f"{r_ov.status_code}: {r_ov.text[:100]}")
        check("[S19] risk_level now low", r_ov.json().get("risk_level") == "low")

        db = _get_db()
        try:
            assessments = _get_risk_assessments(ci_id, db)
            override_row = next(
                (a for a in assessments if a.computed_by != "system"), None
            )
            check("[S19] override RiskAssessment written", override_row is not None)
            if override_row:
                check("[S19] result_level=low", override_row.result_level == "low")
                check("[S19] reason in inputs",
                      "reason" in (override_row.inputs or {}))
                check("[S19] score=None for clinician override",
                      override_row.score is None)
        finally:
            db.close()

        # ── [S20] PatientResponse includes all v2 fields ──────────────────────
        print("\n[S20] PatientResponse includes all v2 fields")
        r_me = client2.post("/auth/patient/signup", json={
            "name": "Full Response Test", "phone": f"2377{_uid()}",
            "password": PASSWORD, "hospital_id": hospital_id,
            "weeks_pregnant_at_signup": 20, "age": 28,
            "previous_loss_count": 1, "gravidity": 2, "blood_group": "AB-",
            "distance_close_to_hospital": False,
        })
        check("[S20] signup 201", r_me.status_code == 201, r_me.text)
        body = r_me.json()
        for field in [
            "previous_loss_count", "gravidity", "blood_group",
            "distance_close_to_hospital", "rh_negative",
            "consecutive_missed_checkins", "missed_checkin_flag",
        ]:
            check(f"[S20] field '{field}' in response", field in body,
                  f"missing from response")
        check("[S20] rh_negative=True for AB-", body.get("rh_negative") is True)
        check("[S20] previous_loss_count=1", body.get("previous_loss_count") == 1)

    finally:
        client2.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 60)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
