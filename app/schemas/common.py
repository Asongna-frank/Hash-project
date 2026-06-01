"""Shared authentication schemas."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """Schema for login request."""

    phone: str
    password: str


class TokenResponse(BaseModel):
    """Schema for token response."""

    access_token: str
    token_type: str = "bearer"
    user_type: str

class ChatMessageRequest(BaseModel):
    message: str


class ChatMessageResponse(BaseModel):
    reply: str
    triage_level: str           # "low" | "medium" | "high"
    loss_detected: bool = False


class RiskOverrideRequest(BaseModel):
    new_level: str       # "low" | "medium" | "high"
    reason: str