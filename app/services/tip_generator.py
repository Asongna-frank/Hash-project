# app/services/tip_generator.py
"""Generate a personalized daily tip for a single patient via the LLM service."""

from datetime import date

from app.models.patient import Patient
from app.services.llm_service import llm_service
from app.services.prompts import DAILY_TIP_SYSTEM_PROMPT, POST_LOSS_TIP_SYSTEM_PROMPT

_SMS_HARD_LIMIT = 155  # one Queen SMS unit is 160 chars; keep margin


def _current_week(patient: Patient) -> int:
    """Return gestational week derived from the patient's LMP."""
    days = (date.today() - patient.lmp).days
    return max(1, min(days // 7, 42))


def _lang_name(patient: Patient) -> str:
    lang = (getattr(patient, "language", None) or "en").lower()
    return {"en": "English", "fr": "French", "pt": "Portuguese"}.get(lang, "English")


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

    parts = [
        f"Patient name: {patient.name}",
        f"Gestational age: week {_current_week(patient)} of pregnancy",
        f"Risk level: {patient.risk_level or 'not yet assessed'}",
        f"Age: {patient.age or 'not provided'}",
        f"Number of prior births (parity): {patient.parity}",
        f"Channel: {channel}",
        f"Active conditions / risk factors: {', '.join(conditions) if conditions else 'none flagged'}",
        f"Write the message in: {_lang_name(patient)}",
    ]
    return "\n".join(parts)


def generate_daily_tip(patient: Patient) -> str:
    """Return a personalized tip string ready to deliver to this patient."""
    channel = "sms" if patient.account_type == "choronko" else "app"

    if patient.status == "post_loss":
        system_prompt = POST_LOSS_TIP_SYSTEM_PROMPT
        context = (
            f"Patient name: {patient.name}\n"
            f"Channel: {channel}\n"
            "Situation: this patient has recently experienced a pregnancy loss."
        )
        max_tokens = 80 if channel == "sms" else 200
    else:
        system_prompt = DAILY_TIP_SYSTEM_PROMPT
        context = _build_context(patient, channel)
        max_tokens = 60 if channel == "sms" else 250

    tip = llm_service.classify_message(
        message=context,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=0.8,  # variety across days
    )

    # Hard guard — Queen SMS rejects payloads over 160 chars
    if channel == "sms" and len(tip) > _SMS_HARD_LIMIT:
        tip = tip[:152] + "..."

    return tip
