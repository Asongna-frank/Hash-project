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
