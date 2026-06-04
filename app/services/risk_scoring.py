# app/services/risk_scoring.py
"""
Risk scoring service — v2.0 rubric with graded age and loss-count bands.

Pure function: no DB access, no side effects.
All weights and thresholds are read from app/core/risk_config.py.
"""

from app.core.risk_config import (
    RUBRIC_VERSION,
    AGE_WEIGHTS,
    PREVIOUS_LOSS_WEIGHTS,
    QUESTION_WEIGHTS,
    RISK_THRESHOLDS,
)


def _age_band(age: int) -> str:
    if age >= 40 or age < 16:
        return "ge40_or_lt16"
    if 35 <= age <= 39:
        return "35_to_39"
    return "16_to_34"


def _loss_band(count: int) -> str:
    if count >= 3:
        return "ge3"
    if count == 2:
        return "2"
    if count == 1:
        return "1"
    return "0"


def compute_risk(answers: dict) -> dict:
    """
    Compute patient risk from questionnaire answers (v2 rubric).

    Required keys in `answers`:
        age                     int
        previous_loss_count     int   (number of prior losses)
        weeks_pregnant_at_signup int
        parity                  int
        previous_stillbirth     bool
        previous_caesarean      bool
        previous_preeclampsia   bool
        has_hypertension        bool
        has_diabetes            bool
        has_sickle_cell         bool
        has_hiv                 bool
        has_severe_anaemia      bool
        multiple_pregnancy      bool

    Unscored keys (gravidity, blood_group, distance_close_to_hospital) are
    silently ignored — they contribute 0 by design.

    Returns:
        {
            "score":          int,
            "level":          "low" | "medium" | "high",
            "rubric_version": str,
            "breakdown":      dict[str, int],  # per-factor points
        }
    """
    breakdown: dict[str, int] = {}

    # Graded factors
    breakdown["age"] = AGE_WEIGHTS[_age_band(answers["age"])]
    breakdown["previous_losses"] = PREVIOUS_LOSS_WEIGHTS[
        _loss_band(answers["previous_loss_count"])
    ]

    # Derived booleans
    derived = {
        "first_trimester": answers["weeks_pregnant_at_signup"] < 13,
        "parity_extreme":  answers["parity"] == 0 or answers["parity"] >= 5,
    }

    for key, weight in QUESTION_WEIGHTS.items():
        if key in derived:
            present = derived[key]
        else:
            present = bool(answers.get(key, False))
        breakdown[key] = weight if present else 0

    score = sum(breakdown.values())

    if score >= RISK_THRESHOLDS["high"]:
        level = "high"
    elif score >= RISK_THRESHOLDS["medium"]:
        level = "medium"
    else:
        level = "low"

    return {
        "score":          score,
        "level":          level,
        "rubric_version": RUBRIC_VERSION,
        "breakdown":      breakdown,
    }


def compute_risk_level(answers: dict) -> tuple[str, int, str]:
    """
    Backward-compatible shim for callers that expect (level, score, version).
    New code should use compute_risk() which also returns the breakdown dict.
    """
    result = compute_risk(answers)
    return result["level"], result["score"], result["rubric_version"]
