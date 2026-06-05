"""Message storage helpers — save inbound and outbound messages to messages table."""

from sqlalchemy.orm import Session

from app.models.message import Message


def save_inbound(
    patient_id,
    content: str,
    triage_level: str,
    channel: str = "app",
    message_type: str = "chat",
    source_lang: str | None = None,
    provider_message_id: str | None = None,
    flagged_for_review: bool = False,
) -> Message:
    """
    Create a Message object for a message received FROM the patient.
    Does not commit — caller must commit.

    `content` should be the English (pivot-language) text; `source_lang` records
    the language the patient actually wrote in. `provider_message_id` is the
    inbound SMS provider's id used for idempotency. `flagged_for_review` marks
    messages whose translation failed so a clinician can review the original.
    """
    return Message(
        patient_id=patient_id,
        direction="in",
        channel=channel,
        content=content,
        message_type=message_type,
        triage_level=triage_level,
        source_lang=source_lang,
        provider_message_id=provider_message_id,
        flagged_for_review=flagged_for_review,
    )


def save_outbound(
    patient_id,
    content: str,
    channel: str = "app",
    message_type: str = "chat",
    source_lang: str | None = None,
    author_name: str | None = None,
) -> Message:
    """
    Create a Message object for a message sent FROM the system TO the patient.
    Does not commit — caller must commit.
    Outbound messages are never triaged (triage_level is always null).
    `source_lang` is the language the reply was delivered in (the patient's).
    """
    return Message(
        patient_id=patient_id,
        direction="out",
        channel=channel,
        content=content,
        message_type=message_type,
        author_name=author_name,
        triage_level=None,
        source_lang=source_lang,
    )
