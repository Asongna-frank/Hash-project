# app/routers/hospital_patients.py
"""
Hospital-side patient creation (choronko / SMS patients).

Mounted at /hospital/patients. Choronko patients have no app and cannot
self-signup, so the hospital onboards them here. Identity is phone-only:
no password, and (deliberately) no individual personnel attribution — personnel
have no logins, so any "created_by" would be client-asserted and unverifiable.
The hospital_id always comes from the auth token, never the request body.
"""

import logging
from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.patient import Patient
from app.models.pregnancy import Pregnancy
from app.schemas.patient import HospitalPatientCreate, PatientResponse
from app.services.audit import write_audit
from app.services.patient_onboarding import onboard_patient
from app.services.post_loss import activate_post_loss, mark_live_birth
from app.utils.access import require_hospital
from app.utils.phone import normalize_phone_or_422

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=PatientResponse,
    status_code=201,
    summary="Create a choronko patient (hospital)",
    description=(
        "Hospital-only. Onboards an SMS (choronko) patient: account_type=choronko, "
        "no password, hospital_id taken from the token (a body hospital_id is "
        "ignored — a hospital cannot create patients elsewhere). Phone is "
        "normalized to E.164 and must be unique. Runs baseline risk scoring and "
        "creates the pregnancy record, same as self-signup. No personnel "
        "attribution is stored. Patients calling this get 403."
    ),
)
def hospital_create_patient(
    body: HospitalPatientCreate,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    # Normalize + enforce unique phone
    patient_phone = normalize_phone_or_422(body.phone, "phone")
    if db.query(Patient).filter(Patient.phone == patient_phone).first():
        raise HTTPException(status_code=400, detail="Phone number already registered")

    # Onboard via the shared service — choronko, no password, hospital from TOKEN.
    data = {**body.model_dump(), "phone": patient_phone}
    patient = onboard_patient(
        db, data,
        hospital_id=caller_hospital_id,
        account_type="choronko",
        hashed_password=None,
    )

    # Audit: hospital created the patient. No personnel attribution (by design).
    write_audit(
        db, actor_type="hospital", actor_id=caller_hospital_id,
        action="patient.create", target_type="patient", target_id=patient.id,
        details={"account_type": "choronko"},
    )
    db.commit()
    db.refresh(patient)
    logger.info("Choronko patient created | hospital=%s | patient=%s",
                caller_hospital_id, patient.id)
    return patient


# ── pregnancy outcome (clinician marks Loss / Live Birth) ─────────────────────

class OutcomeRequest(BaseModel):
    """Clinician records the pregnancy outcome. Marking 'loss' is the ONLY way
    the post-loss care track activates (SRS hard rule — never from chat)."""
    outcome: Literal["loss", "live_birth"] = Field(..., examples=["loss"])
    loss_date: Optional[date] = Field(
        default=None, examples=["2026-06-04"],
        description="Date of loss (loss only). Defaults to today.",
    )
    ga_at_loss: Optional[int] = Field(
        default=None, ge=0, le=44, examples=[18],
        description="Gestational age in weeks at loss (loss only). Computed from LMP if omitted.",
    )

    model_config = ConfigDict(json_schema_extra={"examples": [
        {"outcome": "loss", "loss_date": "2026-06-04", "ga_at_loss": 18},
        {"outcome": "live_birth"},
    ]})


@router.post(
    "/{patient_id}/outcome",
    response_model=PatientResponse,
    summary="Mark pregnancy outcome (Loss / Live Birth)",
    description=(
        "Hospital-only, own patients only (others → 404). Marking 'loss' "
        "activates the post-loss care track (M9): routine tips/check-ins/EDD "
        "stop, risk escalates to high with an audit trail, and Dr Elvira's "
        "approved opener is sent on the patient's own channel (app push or "
        "SMS for choronko). This endpoint is the ONLY way the post-loss track "
        "activates — chat detection only flags for review. Marking 'live_birth' "
        "stops routine pregnancy messaging and sets status=delivered. "
        "A patient already in that state returns 409."
    ),
)
def mark_pregnancy_outcome(
    patient_id: UUID,
    body: OutcomeRequest,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    # Out-of-scope patients look non-existent (no cross-hospital id leaks).
    if not patient or str(patient.hospital_id) != caller_hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    pregnancy = (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient.id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )

    if body.outcome == "loss":
        if patient.status == "post_loss":
            raise HTTPException(status_code=409, detail="Patient is already on the post-loss track")
        activate_post_loss(
            db, patient, pregnancy,
            activated_by=caller_hospital_id,
            loss_date=body.loss_date,
            ga_at_loss=body.ga_at_loss,
        )
    else:  # live_birth
        if patient.status == "delivered":
            raise HTTPException(status_code=409, detail="Live birth already recorded")
        mark_live_birth(db, patient, pregnancy)

    write_audit(
        db, actor_type="hospital", actor_id=caller_hospital_id,
        action=f"pregnancy.outcome.{body.outcome}", target_type="patient",
        target_id=patient.id,
        details={"loss_date": str(body.loss_date) if body.loss_date else None,
                 "ga_at_loss": body.ga_at_loss},
    )
    db.commit()
    db.refresh(patient)
    logger.info("Pregnancy outcome marked | hospital=%s | patient=%s | outcome=%s",
                caller_hospital_id, patient.id, body.outcome)
    return patient


# ── Clinician conversation view ───────────────────────────────────────────────

@router.get(
    "/{patient_id}/messages",
    summary="Patient conversation history (clinician view)",
    description=(
        "Hospital-only, own patients only (others → 404). Returns the patient's "
        "full message history newest-first with pagination (?skip=&limit=, max "
        "200). Each item carries direction (in=patient, out=bot), message_type "
        "(chat/checkin/tip/reminder/crisis), triage_level (inbound only), "
        "source_lang (the language the patient wrote in — content is stored as "
        "the English pivot), and flagged_for_review (translation failed — "
        "clinician should read the original). Use this on the per-patient "
        "dashboard page and when reviewing a suspected-loss or red-flag alert."
    ),
)
def patient_conversation(
    patient_id: UUID,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    from app.models.message import Message

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient or str(patient.hospital_id) != caller_hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    limit = min(max(limit, 1), 200)
    q = (
        db.query(Message)
        .filter(Message.patient_id == patient.id)
        .order_by(Message.created_at.desc())
    )
    total = q.count()
    rows = q.offset(skip).limit(limit).all()

    return {
        "total": total,
        "has_more": skip + len(rows) < total,
        "items": [
            {
                "id": str(m.id),
                "direction": m.direction,
                "channel": m.channel,
                "content": m.content,
                "message_type": m.message_type,
                "triage_level": m.triage_level,
                "source_lang": m.source_lang,
                "flagged_for_review": m.flagged_for_review,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ],
    }


# ── Condition graph (green → yellow → red) ────────────────────────────────────

_BAND_BY_TRIAGE = {"high": "red", "medium": "yellow", "low": "green", None: "green"}
_TRIAGE_RANK = {"low": 0, "medium": 1, "high": 2}


@router.get(
    "/{patient_id}/condition-graph",
    summary="Condition graph data (green/yellow/red timeline)",
    description=(
        "Hospital-only, own patients only (others → 404). Daily time series for "
        "the per-patient condition graph: each point has the day's worst inbound "
        "triage level, the resulting colour band (high→red, medium→yellow, else "
        "green), message count, and GA week. Overlay `events` marks risk-level "
        "changes, appointments, and pregnancy outcome on the timeline. Window "
        "defaults to the last 60 days (?days=, max 180), never earlier than "
        "signup."
    ),
)
def condition_graph(
    patient_id: UUID,
    days: int = 60,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    from datetime import timedelta
    from app.models.appointment import Appointment
    from app.models.message import Message
    from app.models.risk_assessment import RiskAssessment

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient or str(patient.hospital_id) != caller_hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    days = min(max(days, 7), 180)
    today = date.today()
    signup_day = patient.created_at.date() if patient.created_at else today
    start = max(today - timedelta(days=days - 1), signup_day)

    # Worst inbound triage + message counts per day, one query.
    messages = (
        db.query(Message)
        .filter(Message.patient_id == patient.id, Message.created_at >= start)
        .all()
    )
    by_day: dict = {}
    for m in messages:
        if not m.created_at:
            continue
        d = m.created_at.date()
        slot = by_day.setdefault(d, {"worst": None, "in": 0, "out": 0})
        slot["in" if m.direction == "in" else "out"] += 1
        if m.direction == "in" and m.triage_level in _TRIAGE_RANK:
            if slot["worst"] is None or _TRIAGE_RANK[m.triage_level] > _TRIAGE_RANK[slot["worst"]]:
                slot["worst"] = m.triage_level

    points = []
    day = start
    while day <= today:
        slot = by_day.get(day, {"worst": None, "in": 0, "out": 0})
        points.append({
            "date": day.isoformat(),
            "band": _BAND_BY_TRIAGE[slot["worst"]],
            "worst_triage": slot["worst"],
            "messages_in": slot["in"],
            "messages_out": slot["out"],
            "ga_weeks": (day - patient.lmp).days // 7 if patient.lmp else None,
        })
        day += timedelta(days=1)

    # Overlay events: risk changes, appointments, pregnancy outcome.
    events = []
    for ra in (
        db.query(RiskAssessment)
        .filter(RiskAssessment.patient_id == patient.id)
        .order_by(RiskAssessment.computed_at.asc())
        .all()
    ):
        if ra.computed_at:
            events.append({
                "date": ra.computed_at.date().isoformat(),
                "type": "risk_change",
                "detail": f"risk → {ra.result_level} ({'system' if ra.computed_by == 'system' else 'clinician'})",
            })
    for appt in (
        db.query(Appointment)
        .filter(Appointment.patient_id == patient.id, Appointment.is_deleted.is_(False))
        .all()
    ):
        events.append({
            "date": appt.appointment_datetime.date().isoformat(),
            "type": "appointment",
            "detail": appt.title,
        })
    pregnancy = (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient.id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )
    if pregnancy and pregnancy.outcome == "loss" and pregnancy.loss_date:
        events.append({"date": pregnancy.loss_date.isoformat(),
                       "type": "loss_marked", "detail": "Pregnancy loss recorded"})

    return {
        "patient_id": str(patient.id),
        "risk_level": patient.risk_level,
        "status": patient.status,
        "missed_checkin_flag": bool(patient.missed_checkin_flag),
        "window": {"start": start.isoformat(), "end": today.isoformat()},
        "points": points,
        "events": sorted(events, key=lambda e: e["date"]),
    }


# ── Post-loss case view (M9 dashboard) ────────────────────────────────────────

@router.get(
    "/{patient_id}/post-loss",
    summary="Post-loss case summary (dashboard banner + timeline)",
    description=(
        "Hospital-only, own patients only (others → 404). Returns the M9 case "
        "for the dashboard's post-loss banner and dedicated view: activation "
        "details, current paced-cadence stage (day1 → 48h → every3days → "
        "weekly), PHQ-2 engagement (offered_at, her response in her own words, "
        "or 'did not engage' after 7 quiet days), opt-out state, and loss "
        "details from the pregnancy record. 404 with detail 'No post-loss "
        "case' when the patient is not on the post-loss track."
    ),
)
def post_loss_case_view(
    patient_id: UUID,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    from datetime import datetime, timezone
    from app.models.post_loss_case import PostLossCase

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient or str(patient.hospital_id) != caller_hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    case = (
        db.query(PostLossCase)
        .filter(PostLossCase.patient_id == patient.id)
        .first()
    )
    if case is None:
        raise HTTPException(status_code=404, detail="No post-loss case for this patient")

    pregnancy = (
        db.query(Pregnancy)
        .filter(Pregnancy.patient_id == patient.id)
        .order_by(Pregnancy.created_at.desc())
        .first()
    )

    # PHQ-2 engagement state for the mental-health summary (SRS 2.7.3).
    now = datetime.now(timezone.utc)
    if case.phq2_offered_at is None:
        phq2_status = "not_yet_offered"   # offered at week 2 post-activation
    elif case.phq2_response is not None:
        phq2_status = "responded"
    else:
        offered = case.phq2_offered_at
        if offered.tzinfo is None:
            offered = offered.replace(tzinfo=timezone.utc)
        phq2_status = ("did_not_engage" if (now - offered).days >= 7 else "awaiting_response")

    activated = case.activated_at
    if activated and activated.tzinfo is None:
        activated = activated.replace(tzinfo=timezone.utc)

    return {
        "patient_id": str(patient.id),
        "activated_at": case.activated_at.isoformat() if case.activated_at else None,
        "activated_by": case.activated_by,
        "days_since_activation": (now - activated).days if activated else None,
        "opener_sent_at": case.opener_sent_at.isoformat() if case.opener_sent_at else None,
        "current_cadence": case.current_cadence,
        "phq2": {
            "status": phq2_status,
            "offered_at": case.phq2_offered_at.isoformat() if case.phq2_offered_at else None,
            "response": case.phq2_response,
            "responded_at": case.phq2_responded_at.isoformat() if case.phq2_responded_at else None,
        },
        "opt_out_status": patient.opt_out_status,
        "loss": {
            "loss_date": pregnancy.loss_date.isoformat() if pregnancy and pregnancy.loss_date else None,
            "ga_at_loss": pregnancy.ga_at_loss if pregnancy else None,
        },
        "notes": case.notes,
    }


# ── Clinician notes (SRS M8: per-patient note form) ───────────────────────────

class NoteCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000,
                      examples=["Called her about the headache alert — BP normal at clinic, advised rest, follow-up Friday."])
    author_name: Optional[str] = Field(default=None, max_length=120, examples=["Dr Elvira"])

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "text": "Called her about the headache alert — advised rest, follow-up Friday.",
        "author_name": "Dr Elvira",
    }]})


def _note_to_dict(n) -> dict:
    return {
        "id": str(n.id),
        "text": n.text,
        "author_name": n.author_name,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.post(
    "/{patient_id}/notes",
    status_code=201,
    summary="Add a clinician note",
    description=(
        "Hospital-only, own patients only (others → 404). Adds a dated note to "
        "the patient's record (audited). author_name is a display signature "
        "(personnel have no individual logins). Notes are append-only — no "
        "edit/delete, so the clinical record cannot be silently rewritten."
    ),
)
def add_patient_note(
    patient_id: UUID,
    body: NoteCreate,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    from app.models.patient_note import PatientNote

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient or str(patient.hospital_id) != caller_hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    note = PatientNote(
        patient_id=patient.id,
        hospital_id=patient.hospital_id,
        text=body.text.strip(),
        author_name=(body.author_name or "").strip() or None,
    )
    db.add(note)
    write_audit(
        db, actor_type="hospital", actor_id=caller_hospital_id,
        action="patient.note.create", target_type="patient", target_id=patient.id,
        details={"author_name": note.author_name},
    )
    db.commit()
    db.refresh(note)
    return _note_to_dict(note)


@router.get(
    "/{patient_id}/notes",
    summary="List clinician notes",
    description=(
        "Hospital-only, own patients only (others → 404). Newest first, "
        "paginated (?skip=&limit=, max 100)."
    ),
)
def list_patient_notes(
    patient_id: UUID,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    caller_hospital_id: str = Depends(require_hospital),
):
    from app.models.patient_note import PatientNote

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_active.is_(True))
        .first()
    )
    if not patient or str(patient.hospital_id) != caller_hospital_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    limit = min(max(limit, 1), 100)
    q = (
        db.query(PatientNote)
        .filter(PatientNote.patient_id == patient.id)
        .order_by(PatientNote.created_at.desc())
    )
    total = q.count()
    rows = q.offset(skip).limit(limit).all()
    return {"total": total, "has_more": skip + len(rows) < total,
            "items": [_note_to_dict(n) for n in rows]}
