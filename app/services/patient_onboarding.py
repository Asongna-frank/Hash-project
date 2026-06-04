"""
Shared patient onboarding — the single code path for creating a patient + its
initial pregnancy + baseline risk-assessment record.

Used by BOTH:
  - public self-signup (smartphone, with a password), and
  - hospital-side creation (choronko, no password).

The two endpoints differ only in identity/auth (who, account_type, password) and
in how hospital_id is resolved; everything downstream (LMP/EDD, risk scoring,
pregnancy + audit-able records) is identical and lives here so the tracks can
never drift.

onboard_patient stages rows (add/flush) but does NOT commit — the caller commits
in one transaction together with its audit row.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.risk_config import RUBRIC_VERSION  # noqa: F401 (kept for parity/readability)
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.models.risk_assessment import RiskAssessment
from app.services.risk_scoring import compute_risk
from app.utils.pregnancy import compute_lmp_and_edd


# Clinical/questionnaire fields shared by both creation paths.
_CLINICAL_FIELDS = (
    "age", "parity", "language", "preferred_support",
    "previous_loss", "previous_loss_count", "previous_stillbirth",
    "previous_caesarean", "previous_preeclampsia", "has_hypertension",
    "has_diabetes", "has_sickle_cell", "has_hiv", "has_severe_anaemia",
    "multiple_pregnancy", "late_anc_initiation", "no_prior_anc",
    "gravidity", "blood_group", "distance_close_to_hospital",
)


def onboard_patient(
    db: Session,
    data: dict,
    *,
    hospital_id,
    account_type: str,
    hashed_password: str | None,
) -> Patient:
    """
    Create a Patient (+ Pregnancy + RiskAssessment) from `data` (questionnaire
    fields). hospital_id, account_type and hashed_password are supplied by the
    caller (NEVER taken from the request body for hospital_id). Phone in `data`
    must already be normalized to E.164. Returns the flushed Patient (no commit).
    """
    weeks = data["weeks_pregnant_at_signup"]
    lmp, edd = compute_lmp_and_edd(weeks)

    blood_group = data.get("blood_group")
    rh_negative = bool(blood_group) and blood_group.endswith("-")

    clinical = {f: data.get(f) for f in _CLINICAL_FIELDS}

    patient = Patient(
        name=data["name"],
        phone=data["phone"],
        hashed_password=hashed_password,
        hospital_id=hospital_id,
        weeks_pregnant_at_signup=weeks,
        lmp=lmp,
        edd=edd,
        account_type=account_type,
        status="active",
        rh_negative=rh_negative,
        **{k: v for k, v in clinical.items() if v is not None},
    )
    db.add(patient)
    db.flush()  # assign patient.id without committing

    # Baseline risk scoring (v2 rubric)
    answers = {
        "age": data.get("age"),
        "previous_loss_count": data.get("previous_loss_count", 0) or 0,
        "weeks_pregnant_at_signup": weeks,
        "parity": data.get("parity", 0) or 0,
        "previous_stillbirth": bool(data.get("previous_stillbirth")),
        "previous_caesarean": bool(data.get("previous_caesarean")),
        "previous_preeclampsia": bool(data.get("previous_preeclampsia")),
        "has_hypertension": bool(data.get("has_hypertension")),
        "has_diabetes": bool(data.get("has_diabetes")),
        "has_sickle_cell": bool(data.get("has_sickle_cell")),
        "has_hiv": bool(data.get("has_hiv")),
        "has_severe_anaemia": bool(data.get("has_severe_anaemia")),
        "multiple_pregnancy": bool(data.get("multiple_pregnancy")),
    }
    result = compute_risk(answers)

    patient.risk_level = result["level"]
    patient.risk_level_set_at = datetime.now(timezone.utc)
    patient.risk_level_set_by = "system"

    db.add(RiskAssessment(
        patient_id=patient.id,
        computed_by="system",
        inputs={**answers, "_breakdown": result["breakdown"]},
        rubric_version=result["rubric_version"],
        result_level=result["level"],
        score=result["score"],
    ))

    db.add(Pregnancy(
        patient_id=patient.id,
        lmp=lmp,
        edd=edd,
        outcome="ongoing",
        routine_paused=False,
    ))

    return patient
