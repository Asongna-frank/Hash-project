"""
Conversation engine — builds patient context, fetches message history,
calls LLM, returns reply and triage level.
"""

import json
import re
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.patient import Patient
from app.models.hospital import Hospital
from app.services.llm_service import llm_service
from app.services.prompts import CONVERSATION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Number of previous messages included in every LLM prompt
HISTORY_WINDOW = 10

# Safe fallback used when LLM returns malformed JSON
FALLBACK_REPLY = (
    "I received your message. "
    "Please contact your hospital directly if this is urgent."
)
FALLBACK_TRIAGE = "medium"


def _build_patient_context(patient: Patient, db: Session) -> str:
    """
    Assemble a plain-text block describing the patient's clinical context.
    Injected into every LLM prompt so the model always knows who it is
    talking to without needing separate lookups.
    """
    current_ga_weeks = (date.today() - patient.lmp).days // 7

    # Fetch hospital name for context
    hospital = db.query(Hospital).filter(
        Hospital.id == patient.hospital_id
    ).first()
    hospital_name = hospital.name if hospital else "Unknown hospital"

    # Build active conditions list from boolean flags
    conditions = []
    condition_map = {
        "previous_loss":        "history of pregnancy loss",
        "previous_stillbirth":  "history of stillbirth",
        "previous_caesarean":   "previous caesarean section",
        "previous_preeclampsia": "history of pre-eclampsia",
        "has_hypertension":     "hypertension",
        "has_diabetes":         "diabetes",
        "has_sickle_cell":      "sickle cell disease",
        "has_hiv":              "HIV",
        "has_severe_anaemia":   "severe anaemia",
        "multiple_pregnancy":   "multiple pregnancy (twins or more)",
        "late_anc_initiation":  "late antenatal care initiation",
        "no_prior_anc":         "no prior antenatal care",
    }
    for field, label in condition_map.items():
        if getattr(patient, field, False):
            conditions.append(label)

    conditions_text = (
        ", ".join(conditions) if conditions else "none recorded"
    )

    return (
        f"Patient name: {patient.name}\n"
        f"Gestational week: {current_ga_weeks}\n"
        f"Risk level: {patient.risk_level or 'not yet assessed'}\n"
        f"Hospital: {hospital_name}\n"
        f"Status: {patient.status}\n"
        f"Known conditions: {conditions_text}\n"
        f"Age: {patient.age or 'not recorded'}\n"
        f"Parity: {patient.parity} prior birth(s)"
    )


def _fetch_history(patient_id, db: Session) -> str:
    """
    Fetch the last HISTORY_WINDOW messages for this patient ordered
    oldest-first so the LLM reads them chronologically.
    Returns a formatted string ready for injection into the prompt.
    """
    messages = (
        db.query(Message)
        .filter(Message.patient_id == patient_id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_WINDOW)
        .all()
    )
    messages = list(reversed(messages))  # oldest first

    if not messages:
        return "No previous conversation."

    lines = []
    for msg in messages:
        speaker = "Patient" if msg.direction == "in" else "Bot"
        lines.append(f"{speaker}: {msg.content}")

    return "\n".join(lines)


def _build_system_prompt(patient_context: str, history: str) -> str:
    """
    Assemble the full system prompt sent to the LLM on every chat call.
    Uses CONVERSATION_SYSTEM_PROMPT from prompts.py for consistency and easy updates.
    """
    return f"""{CONVERSATION_SYSTEM_PROMPT}

Patient context:
{patient_context}

Recent conversation history:
{history}

LANGUAGE RULE (critical): write the "reply" value in the SAME LANGUAGE as the
patient's message you are answering. If she wrote in French, the reply must be
entirely in French. Do not copy example responses verbatim — adapt them to her
language and situation.

CRITICAL OUTPUT RULE: Your ENTIRE output must be a single JSON object and
nothing else — your first character must be {{ and your last character must
be }}. Never write the reply text outside the JSON. No explanation, no
markdown, no code block. Exact format:
{{"reply": "your response to the patient here", "triage_level": "low or medium or high"}}"""


def _parse_llm_response(raw: str) -> tuple[str, str]:
    """
    Parse the LLM JSON response into (reply, triage_level).
    Returns safe fallback values if parsing fails.
    """
    try:
        # Strip any accidental markdown fences the model may add
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            # Model wrote text around the JSON (e.g. the reply first, then the
            # object). Extract the outermost {...} block and parse that.
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))

        reply = str(parsed.get("reply", FALLBACK_REPLY)).strip()
        triage = str(parsed.get("triage_level", FALLBACK_TRIAGE)).lower().strip()

        if triage not in ("low", "medium", "high"):
            logger.warning(
                "LLM returned unexpected triage_level '%s' — defaulting to medium",
                triage,
            )
            triage = FALLBACK_TRIAGE

        return reply, triage

    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error(
            "Failed to parse LLM response: %s | Raw response: %s", exc, raw
        )
        return FALLBACK_REPLY, FALLBACK_TRIAGE


def generate_reply(
    patient: Patient,
    inbound_message: str,
    db: Session,
) -> tuple[str, str]:
    """
    Main entry point for the conversation engine.

    Args:
        patient: the Patient ORM object
        inbound_message: the text the patient just sent
        db: database session

    Returns:
        (reply, triage_level)
        reply: the bot's response text
        triage_level: "low" | "medium" | "high"
    """
    patient_context = _build_patient_context(patient, db)
    history = _fetch_history(patient.id, db)
    system_prompt = _build_system_prompt(patient_context, history)

    try:
        raw = llm_service.classify_message(
            message=inbound_message,
            system_prompt=system_prompt,
        )
    except Exception as exc:  # noqa: BLE001 — LLM outage must never crash the chat.
        # Deterministic red flags (chat_core) still force HIGH + alert without the LLM.
        logger.error("LLM call failed — using fallback reply: %s", exc)
        return FALLBACK_REPLY, FALLBACK_TRIAGE

    reply, triage_level = _parse_llm_response(raw)

    logger.info(
        "Conversation reply generated | patient=%s | triage=%s",
        patient.id,
        triage_level,
    )

    return reply, triage_level
