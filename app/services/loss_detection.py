# app/services/loss_detection.py
import logging
from app.services.prompts import LOSS_KEYWORDS, LOSS_DETECTION_SYSTEM_PROMPT
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)


def _keyword_match(message: str) -> bool:
    """
    Layer 1 — fast keyword scan with no API cost.
    Returns True if any known loss keyword is found in the message.
    """
    msg_lower = message.lower()
    return any(keyword in msg_lower for keyword in LOSS_KEYWORDS)


def detect_loss(message: str) -> str:
    """
    Two-layer loss detection pipeline.

    Layer 1: keyword matching — returns NOT_A_LOSS immediately if no match.
    Layer 2: LLM confirmation via llm_service — only called if Layer 1 fires.

    Returns one of:
        "CONFIRMED"   — patient is clearly reporting a pregnancy loss
        "AMBIGUOUS"   — unclear, bot should ask a follow-up question
        "NOT_A_LOSS"  — not a loss report, route through normal triage
    """
    if not _keyword_match(message):
        return "NOT_A_LOSS"

    logger.info("Loss keyword matched — calling LLM for confirmation")

    try:
        result = llm_service.classify_message(
            message=message,
            system_prompt=LOSS_DETECTION_SYSTEM_PROMPT,
            max_tokens=10,
        )
        result_upper = result.strip().upper()

        if result_upper in ("CONFIRMED", "AMBIGUOUS", "NOT_A_LOSS"):
            logger.info("LLM loss detection result: %s", result_upper)
            return result_upper

        # Unexpected LLM response — treat as ambiguous, never auto-activate
        logger.warning(
            "Unexpected LLM response for loss detection: '%s' — defaulting to AMBIGUOUS",
            result,
        )
        return "AMBIGUOUS"

    except Exception as exc:
        # If LLM call fails, default to AMBIGUOUS — never silently skip a possible loss
        logger.error("LLM loss detection failed: %s — defaulting to AMBIGUOUS", exc)
        return "AMBIGUOUS"