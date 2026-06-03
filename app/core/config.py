"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""

    # Queen SMS (choronko track)
    QUEEN_SMS_API_KEY: str
    QUEEN_SMS_SENDER_ID: str = "HASH"            # max 11 chars, must be an APPROVED sender id
    QUEEN_SMS_BASE_URL: str = "https://api.queensms.net/v1"

    # Future AWS fields — add when AWS is set up:
    # AWS_REGION: str = "us-east-1"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Module-level settings instance
settings = Settings()
