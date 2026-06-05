# app/core/risk_config.py
# ============================================================
# RISK SCORING RUBRIC v2.0 — edited by clinical lead (Dr Elvira)
# Change values here only. Bump RUBRIC_VERSION when any value changes.
# The scoring function reads every value from here — never hardcode.
# ============================================================

RUBRIC_VERSION = "v2.0"

# Graded factors: the answer selects a band; the band gives the points.
AGE_WEIGHTS: dict[str, int] = {
    "ge40_or_lt16": 4,  # age >= 40 OR age < 16
    "35_to_39":     2,  # age 35-39
    "16_to_34":     0,  # age 16-34 (safe range)
}

PREVIOUS_LOSS_WEIGHTS: dict[str, int] = {
    "ge3": 5,  # 3 or more prior losses
    "2":   3,
    "1":   2,
    "0":   0,
}

# Flat boolean factors: present → points, absent → 0.
QUESTION_WEIGHTS: dict[str, int] = {
    "has_sickle_cell":        4,
    "has_hypertension":       3,
    "has_diabetes":           3,
    "previous_stillbirth":    3,
    "previous_preeclampsia":  3,
    "multiple_pregnancy":     3,
    "has_hiv":                2,
    "has_severe_anaemia":     2,
    "previous_caesarean":     1,
    "first_trimester":        1,  # derived: weeks_pregnant_at_signup < 13
    "parity_extreme":         1,  # derived: parity == 0 OR parity >= 5
    # Deliberately NOT scored (weight 0):
    #   late_anc_initiation, no_prior_anc  — legacy placeholders, dropped from rubric
    #   gravidity                          — double-counts with parity + losses
    #   blood_group                        — Rh-negative raises a clinical flag, not a score
    #   distance_close_to_hospital         — feeds emergency logic, not baseline risk
}

RISK_THRESHOLDS: dict[str, int] = {
    "high":   9,   # score >= 9 → high
    "medium": 4,   # score >= 4 → medium
                   # score < 4  → low
}

# Proactive check-in cadence in DAYS, keyed by risk level.
# High ≈ 2× Medium frequency (7÷2 = 3.5 → 3, erring toward more contact).
CHECK_IN_CADENCE_DAYS: dict[str, int] = {
    "high":   1,   # daily — per SRS acceptance criteria (was 3, pre-SRS value)
    "medium": 7,   # weekly
    "low":    14,  # fortnightly (+ milestone-week overrides in checkin_sender)
}

# Consecutive missed check-ins (no patient reply before next check-in) that
# trigger a clinician dashboard flag. Low-risk is not escalated in MVP.
MISSED_CHECKIN_ESCALATION: dict[str, int] = {
    "high":   3,
    "medium": 2,
}
