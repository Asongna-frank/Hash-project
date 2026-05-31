# app/core/risk_config.py
# ============================================================
# RISK SCORING RUBRIC — edited by clinical lead (Dr Elvira)
# Change values here. Do not change the scoring function.
# Bump RUBRIC_VERSION when any value changes.
# ============================================================

RUBRIC_VERSION = "v1.0"

QUESTION_WEIGHTS: dict[str, int] = {
    "age_outside_range":       3,   # patient age is ≤17 or ≥35
    "previous_loss":           3,   # previous miscarriage
    "previous_stillbirth":     4,
    "previous_caesarean":      2,
    "previous_preeclampsia":   4,
    "has_hypertension":        4,
    "has_diabetes":            4,
    "has_sickle_cell":         3,
    "has_hiv":                 3,
    "has_severe_anaemia":      3,
    "multiple_pregnancy":      3,   # twins or more
    "late_anc_initiation":     2,   # started antenatal care after week 20
    "no_prior_anc":            3,   # never had antenatal care before
}

RISK_THRESHOLDS: dict[str, int] = {
    "high":   8,   # total score >= 8  → high
    "medium": 4,   # total score >= 4  → medium
                   # total score < 4   → low
}
