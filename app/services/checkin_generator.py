# app/services/checkin_generator.py
"""Generate a personalized proactive wellness check-in for a single patient via the LLM."""

from datetime import date

from app.models.patient import Patient
from app.services.llm_service import llm_service
from app.services.prompts import CHECKIN_SYSTEM_PROMPT, POST_LOSS_CHECKIN_SYSTEM_PROMPT

_SMS_HARD_LIMIT = 155  # one Queen SMS unit is 160 chars; keep a safety margin

# Gestational weeks considered clinically significant for low-risk patients.
# A check-in is sent when the patient enters one of these weeks even if the
# normal fortnightly interval has not elapsed yet.
_MILESTONE_WEEKS: frozenset[int] = frozenset({12, 20, 28, 36})


def _current_week(patient: Patient) -> int:
    """Return gestational week derived from the patient's stored LMP."""
    days = (date.today() - patient.lmp).days
    return max(1, min(days // 7, 42))


def _build_context(patient: Patient, channel: str) -> str:
    conditions = []
    if patient.has_hypertension:       conditions.append("hypertension")
    if patient.has_diabetes:           conditions.append("gestational/pre-existing diabetes")
    if patient.has_sickle_cell:        conditions.append("sickle cell disease")
    if patient.has_hiv:                conditions.append("HIV-positive")
    if patient.has_severe_anaemia:     conditions.append("severe anaemia")
    if patient.multiple_pregnancy:     conditions.append("multiple pregnancy (twins or more)")
    if patient.previous_loss:          conditions.append("history of pregnancy loss")
    if patient.previous_stillbirth:    conditions.append("history of stillbirth")
    if patient.previous_caesarean:     conditions.append("history of caesarean section")
    if patient.previous_preeclampsia:  conditions.append("history of pre-eclampsia")
    if patient.late_anc_initiation:    conditions.append("late ANC initiation")
    if patient.no_prior_anc:           conditions.append("no prior ANC visits this pregnancy")

    week = _current_week(patient)
    parts = [
        f"Patient name: {patient.name}",
        f"Gestational age: week {week} of pregnancy",
        f"Risk level: {patient.risk_level or 'not yet assessed'}",
        f"Age: {patient.age or 'not provided'}",
        f"Number of prior births (parity): {patient.parity}",
        f"Channel: {channel}",
        f"Active conditions / risk factors: {', '.join(conditions) if conditions else 'none flagged'}",
    ]
    if week in _MILESTONE_WEEKS:
        parts.append(
            f"Note: week {week} is a clinically significant milestone — acknowledge it naturally in the check-in"
        )
    return "\n".join(parts)


def generate_checkin(patient: Patient) -> str:
    """Return a personalized check-in string ready to deliver to this patient."""
    channel = "sms" if patient.account_type == "choronko" else "app"

    if patient.status == "post_loss":
        system_prompt = POST_LOSS_CHECKIN_SYSTEM_PROMPT
        context = (
            f"Patient name: {patient.name}\n"
            f"Channel: {channel}\n"
            "Situation: this patient has recently experienced a pregnancy loss."
        )
        max_tokens = 80 if channel == "sms" else 200
    else:
        system_prompt = CHECKIN_SYSTEM_PROMPT
        context = _build_context(patient, channel)
        max_tokens = 60 if channel == "sms" else 250

    msg = llm_service.classify_message(
        message=context,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=0.7,  # enough variety across days without going off-topic
    )

    if channel == "sms" and len(msg) > _SMS_HARD_LIMIT:
        msg = msg[:152] + "..."

    return msg
