"""Pregnancy-related utility functions."""

from datetime import date, timedelta


def compute_lmp_and_edd(weeks_pregnant: int) -> tuple[date, date]:
    """
    Back-calculate LMP and forward-calculate EDD from weeks pregnant at signup.

    Args:
        weeks_pregnant: int, 1–42. How many weeks pregnant the patient
                        reports on the day they create their account.

    Returns:
        (lmp, edd) as Python date objects.

    Logic:
        lmp = today - (weeks_pregnant * 7 days)
        edd = lmp + 280 days   (40 weeks = full term)
    """
    today = date.today()
    lmp = today - timedelta(weeks=weeks_pregnant)
    edd = lmp + timedelta(days=280)
    return lmp, edd
