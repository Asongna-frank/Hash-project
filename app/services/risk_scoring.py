# app/services/risk_scoring.py
"""Risk scoring service for computing patient risk level from questionnaire."""

from app.core.risk_config import QUESTION_WEIGHTS, RISK_THRESHOLDS, RUBRIC_VERSION


def compute_risk_level(answers: dict) -> tuple[str, int, str]:
    """
    Compute patient risk level from questionnaire answers.

    Args:
        answers: dict mapping question keys to bool values.
                 Keys must match QUESTION_WEIGHTS in risk_config.py.
                 age_outside_range is computed before calling this function
                 (True if patient age <= 17 or >= 35).

    Returns:
        (risk_level, score, rubric_version)
        risk_level: "low" | "medium" | "high"
        score: raw integer point total
        rubric_version: string from risk_config.RUBRIC_VERSION
    """
    score = 0
    for question_key, weight in QUESTION_WEIGHTS.items():
        if answers.get(question_key, False):
            score += weight

    if score >= RISK_THRESHOLDS["high"]:
        level = "high"
    elif score >= RISK_THRESHOLDS["medium"]:
        level = "medium"
    else:
        level = "low"

    return level, score, RUBRIC_VERSION
