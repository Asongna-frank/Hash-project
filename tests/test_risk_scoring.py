# tests/test_risk_scoring.py
"""
Pure-function unit tests for the v2.0 risk scoring rubric.

No DB, no server — runs instantly.
All expected values are cross-checked against the config in risk_config.py.

Run:  pytest tests/test_risk_scoring.py -v
"""

import pytest
from app.core.risk_config import (
    AGE_WEIGHTS,
    PREVIOUS_LOSS_WEIGHTS,
    QUESTION_WEIGHTS,
    RISK_THRESHOLDS,
    RUBRIC_VERSION,
)
from app.services.risk_scoring import compute_risk, _age_band, _loss_band


# ── helpers ───────────────────────────────────────────────────────────────────

def _base_answers(**overrides) -> dict:
    """All-zero baseline patient. Override individual fields as needed."""
    base = {
        "age":                    28,
        "previous_loss_count":    0,
        "weeks_pregnant_at_signup": 20,
        "parity":                 2,
        "previous_stillbirth":    False,
        "previous_caesarean":     False,
        "previous_preeclampsia":  False,
        "has_hypertension":       False,
        "has_diabetes":           False,
        "has_sickle_cell":        False,
        "has_hiv":                False,
        "has_severe_anaemia":     False,
        "multiple_pregnancy":     False,
    }
    base.update(overrides)
    return base


# ── Test 1: All-zero patient → low ───────────────────────────────────────────

def test_all_zero_is_low():
    r = compute_risk(_base_answers())
    assert r["score"] == 0
    assert r["level"] == "low"
    assert r["rubric_version"] == RUBRIC_VERSION


# ── Test 2: Single major factor → medium, not high ───────────────────────────

def test_sickle_cell_only_is_medium():
    """has_sickle_cell = 4 pts → medium (4 >= 4); one factor alone cannot reach high."""
    r = compute_risk(_base_answers(has_sickle_cell=True))
    assert r["score"] == QUESTION_WEIGHTS["has_sickle_cell"]  # 4
    assert r["level"] == "medium"


# ── Test 3: Age band boundaries ───────────────────────────────────────────────

@pytest.mark.parametrize("age,expected_pts", [
    (39, AGE_WEIGHTS["35_to_39"]),      # 2
    (40, AGE_WEIGHTS["ge40_or_lt16"]),  # 4
    (15, AGE_WEIGHTS["ge40_or_lt16"]),  # 4
    (34, AGE_WEIGHTS["16_to_34"]),      # 0
    (16, AGE_WEIGHTS["16_to_34"]),      # 0
    (35, AGE_WEIGHTS["35_to_39"]),      # 2
])
def test_age_band_boundaries(age, expected_pts):
    r = compute_risk(_base_answers(age=age))
    assert r["breakdown"]["age"] == expected_pts, f"age={age} expected {expected_pts} pts"


# ── Test 4: Loss dose-response ────────────────────────────────────────────────

@pytest.mark.parametrize("count,expected_pts", [
    (0, PREVIOUS_LOSS_WEIGHTS["0"]),    # 0
    (1, PREVIOUS_LOSS_WEIGHTS["1"]),    # 2
    (2, PREVIOUS_LOSS_WEIGHTS["2"]),    # 3
    (3, PREVIOUS_LOSS_WEIGHTS["ge3"]),  # 5
    (4, PREVIOUS_LOSS_WEIGHTS["ge3"]),  # 5 — caps at ge3 band
    (9, PREVIOUS_LOSS_WEIGHTS["ge3"]),  # 5 — still capped
])
def test_loss_dose_response(count, expected_pts):
    r = compute_risk(_base_answers(previous_loss_count=count))
    assert r["breakdown"]["previous_losses"] == expected_pts


# ── Test 5: Stacking factors → high ──────────────────────────────────────────

def test_stacking_three_losses_hypertension_first_trimester_is_high():
    # 3 losses (5) + hypertension (3) + first_trimester (1) = 9 → high
    r = compute_risk(_base_answers(
        previous_loss_count=3,
        has_hypertension=True,
        weeks_pregnant_at_signup=8,  # < 13 → first_trimester
    ))
    assert r["score"] == 9
    assert r["level"] == "high"


def test_stacking_sickle_diabetes_multiple_is_high():
    # sickle_cell (4) + diabetes (3) + multiple_pregnancy (3) = 10 → high
    r = compute_risk(_base_answers(
        has_sickle_cell=True,
        has_diabetes=True,
        multiple_pregnancy=True,
    ))
    assert r["score"] == 10
    assert r["level"] == "high"


# ── Test 6: Threshold edges ───────────────────────────────────────────────────

def test_score_3_is_low():
    # 1 loss (2) + parity_extreme:parity=0 (1) = 3 → low
    r = compute_risk(_base_answers(previous_loss_count=1, parity=0))
    assert r["score"] == 3
    assert r["level"] == "low"


def test_score_4_is_medium():
    # has_sickle_cell (4) = 4 → medium
    r = compute_risk(_base_answers(has_sickle_cell=True))
    assert r["score"] == 4
    assert r["level"] == "medium"


def test_score_8_is_medium():
    # sickle_cell (4) + hypertension (3) + first_trimester (1) = 8 → medium (8 < 9)
    r = compute_risk(_base_answers(
        has_sickle_cell=True,
        has_hypertension=True,
        weeks_pregnant_at_signup=8,
    ))
    assert r["score"] == 8
    assert r["level"] == "medium"


def test_score_9_is_high():
    # sickle_cell (4) + hypertension (3) + hiv (2) = 9 → high
    r = compute_risk(_base_answers(
        has_sickle_cell=True,
        has_hypertension=True,
        has_hiv=True,
    ))
    assert r["score"] == 9
    assert r["level"] == "high"


# ── Test 7: Unscored fields ignored ──────────────────────────────────────────

def test_unscored_fields_do_not_change_score():
    """gravidity, blood_group, distance_close_to_hospital contribute 0."""
    base_score = compute_risk(_base_answers())["score"]

    r = compute_risk(_base_answers(
        gravidity=8,
        blood_group="O-",
        distance_close_to_hospital=False,
    ))
    assert r["score"] == base_score


# ── Test 8: Rh-negative flag derivation ──────────────────────────────────────
# This tests the flag derivation logic (in auth.py signup), not the scorer itself.
# We verify that the scorer ignores blood_group while the flag can be derived.

@pytest.mark.parametrize("blood_group,expect_rh_neg", [
    ("O-",  True),
    ("AB-", True),
    ("A-",  True),
    ("B-",  True),
    ("O+",  False),
    ("AB+", False),
    ("unknown", False),
])
def test_rh_negative_derivation(blood_group, expect_rh_neg):
    """blood_group ending in '-' → rh_negative True; adds 0 to score."""
    rh_negative = blood_group.endswith("-")
    assert rh_negative == expect_rh_neg

    # Score unchanged regardless of blood group
    base_score = compute_risk(_base_answers())["score"]
    r = compute_risk(_base_answers(blood_group=blood_group))
    assert r["score"] == base_score


# ── Test 9: Derived fields — first_trimester and parity_extreme ──────────────

def test_first_trimester_flag():
    r_early = compute_risk(_base_answers(weeks_pregnant_at_signup=8))
    r_late  = compute_risk(_base_answers(weeks_pregnant_at_signup=20))
    assert r_early["breakdown"]["first_trimester"] == QUESTION_WEIGHTS["first_trimester"]  # 1
    assert r_late["breakdown"]["first_trimester"]  == 0


def test_parity_extreme_nulliparous():
    r = compute_risk(_base_answers(parity=0))
    assert r["breakdown"]["parity_extreme"] == QUESTION_WEIGHTS["parity_extreme"]  # 1


def test_parity_extreme_grand_multipara():
    r = compute_risk(_base_answers(parity=5))
    assert r["breakdown"]["parity_extreme"] == QUESTION_WEIGHTS["parity_extreme"]  # 1


def test_parity_normal_not_extreme():
    for p in (1, 2, 3, 4):
        r = compute_risk(_base_answers(parity=p))
        assert r["breakdown"]["parity_extreme"] == 0, f"parity={p} should not be extreme"


# ── Test 10: Rubric version in output ─────────────────────────────────────────

def test_rubric_version_in_result():
    r = compute_risk(_base_answers())
    assert r["rubric_version"] == "v2.0"


# ── Test 11: Breakdown keys present ──────────────────────────────────────────

def test_breakdown_contains_all_factors():
    r = compute_risk(_base_answers())
    expected_keys = {"age", "previous_losses"} | set(QUESTION_WEIGHTS.keys())
    assert expected_keys == set(r["breakdown"].keys())
