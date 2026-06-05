# app/services/takeover.py
"""
Clinician chat takeover — a human takes over the conversation, then hands
back to the bot.

While a takeover is active for a patient:
  - the bot does NOT reply to her messages;
  - her messages are relayed in real time to the hospital dashboard
    ({"type": "patient_message"} on /alerts/ws) so the doctor chats live;
  - the deterministic red-flag layer STILL runs on every inbound message
    (a danger sign during a human chat still raises the alert + audit trail);
  - the doctor replies via POST /hospital/patients/{id}/messages.

State is in-memory (single worker, same as calls/alerts managers). A server
restart ends all takeovers — the safe default: the bot resumes.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Takeover:
    patient_id: str
    hospital_id: str
    author_name: Optional[str] = None       # "Dr Elvira" — display only
    hospital_name: str = "Your hospital"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TakeoverManager:
    def __init__(self) -> None:
        self._active: dict[str, Takeover] = {}   # patient_id -> Takeover

    def start(self, patient_id: str, hospital_id: str,
              author_name: Optional[str], hospital_name: str) -> Takeover:
        t = Takeover(patient_id=patient_id, hospital_id=hospital_id,
                     author_name=author_name, hospital_name=hospital_name)
        self._active[patient_id] = t
        logger.info("Takeover START | patient=%s | by=%s (%s)",
                    patient_id, author_name, hospital_id)
        return t

    def end(self, patient_id: str) -> Optional[Takeover]:
        t = self._active.pop(patient_id, None)
        if t:
            logger.info("Takeover END | patient=%s", patient_id)
        return t

    def get(self, patient_id: str) -> Optional[Takeover]:
        return self._active.get(patient_id)

    def is_active(self, patient_id: str) -> bool:
        return patient_id in self._active


takeovers = TakeoverManager()
