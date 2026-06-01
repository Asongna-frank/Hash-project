"""Message storage helpers — save inbound and outbound messages to messages table."""

from sqlalchemy.orm import Session

from app.models.message import Message


def save_inbound(
    patient_id,
    content: str,
    triage_level: str,
    channel: str = "app",
    message_type: str = "chat",
) -> Message:
    """
    Create a Message object for a message received FROM the patient.
    Does not commit — caller must commit.
    """
    return Message(
        patient_id=patient_id,
        direction="in",
        channel=channel,
        content=content,
        message_type=message_type,
        triage_level=triage_level,
    )


def save_outbound(
    patient_id,
    content: str,
    channel: str = "app",
    message_type: str = "chat",
) -> Message:
    """
    Create a Message object for a message sent FROM the system TO the patient.
    Does not commit — caller must commit.
    Outbound messages are never triaged (triage_level is always null).
    """
    return Message(
        patient_id=patient_id,
        direction="out",
        channel=channel,
        content=content,
        message_type=message_type,
        triage_level=None,
    )
