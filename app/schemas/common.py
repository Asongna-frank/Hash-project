"""Shared authentication and chat schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Login for hospitals and patients.

    Patients log in with phone + password. Hospitals may use phone OR email
    + password (exactly one identifier is required).
    """

    phone: Optional[str] = Field(default=None, examples=["+237679977660"])
    email: Optional[str] = Field(default=None, examples=["contact@gh-douala.cm"])
    password: str = Field(..., examples=["StrongPass123!"])

    model_config = {
        "json_schema_extra": {
            "examples": [{"phone": "+237679977660", "password": "StrongPass123!"}]
        }
    }


class TokenResponse(BaseModel):
    """JWT issued on successful login."""

    access_token: str = Field(
        ...,
        examples=[
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIrMjM3Njc5OTc3NjYwIiwidHlwZSI6InBhdGllbnQifQ."
            "3Tn8xq1m0pQ-fakeSignature-Xy7Zb"
        ],
    )
    token_type: str = Field(default="bearer", examples=["bearer"])
    user_type: str = Field(..., examples=["patient"])

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIrMjM3Njc5OTc3NjYwIn0.fake-signature",
                "token_type": "bearer",
                "user_type": "patient",
            }]
        }
    }


class ChatMessageRequest(BaseModel):
    """A single inbound chat message from a patient."""

    message: str = Field(..., examples=["I have had mild lower back pain since yesterday. Should I be worried?"])

    model_config = {
        "json_schema_extra": {
            "examples": [{"message": "I have had mild lower back pain since yesterday. Should I be worried?"}]
        }
    }


class ChatMessageResponse(BaseModel):
    """The bot's reply, with the acuity assigned to the patient's message."""

    reply: str = Field(
        ...,
        examples=["Maria, mild back discomfort is common in pregnancy, but tell me if it becomes severe or comes with bleeding. If it worsens, please contact your clinician."],
    )
    triage_level: str = Field(..., examples=["low"])  # "low" | "medium" | "high"
    loss_detected: bool = Field(default=False, examples=[False])
    is_crisis: bool = Field(default=False, examples=[False])  # True only for a stored pre-approved crisis message

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "reply": "Maria, mild back discomfort is common in pregnancy, but tell me if it becomes severe or comes with bleeding. If it worsens, please contact your clinician.",
                "triage_level": "low",
                "loss_detected": False,
                "is_crisis": False,
            }]
        }
    }


class RiskOverrideRequest(BaseModel):
    """Clinician override of a patient's risk level, with an auditable reason."""

    new_level: str = Field(..., examples=["high"])  # "low" | "medium" | "high"
    reason: str = Field(..., examples=["Patient reported new severe headaches and elevated BP at last visit."])

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "new_level": "high",
                "reason": "Patient reported new severe headaches and elevated BP at last visit.",
            }]
        }
    }
