# app/utils/phone.py
"""
Phone-number normalization to E.164.

Every phone entry point (patient/hospital/personnel signup, hospital-creates-
appointment-by-phone, and the inbound-SMS sender lookup) must normalize through
`normalize_phone` so stored numbers and lookups always match in one canonical
form. Numbers without a leading "+" are parsed using DEFAULT_PHONE_REGION
(Cameroon by default); numbers with "+" are parsed as-is.

`normalize_phone` raises ValueError on an invalid number — callers in routers
translate that into a 422 response.
"""

import phonenumbers
from fastapi import HTTPException, status

from app.core.config import settings


def normalize_phone(raw: str) -> str:
    """
    Parse and validate `raw`, returning it in E.164 form (e.g. +237677123456).

    Raises:
        ValueError: if the input is empty or not a valid phone number.
    """
    if raw is None or not str(raw).strip():
        raise ValueError("Phone number is required")

    candidate = str(raw).strip()
    # Region is only applied when there is no explicit country code ("+").
    region = None if candidate.startswith("+") else settings.DEFAULT_PHONE_REGION

    try:
        parsed = phonenumbers.parse(candidate, region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"Invalid phone number: {exc}") from exc

    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(f"Invalid phone number: {raw!r}")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_phone_or_422(raw: str, field: str = "phone") -> str:
    """
    Router-facing wrapper: normalize `raw` or raise HTTP 422 with a clear message.
    Use at every request entry point that accepts a phone number.
    """
    try:
        return normalize_phone(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {field}: {exc}",
        ) from exc
