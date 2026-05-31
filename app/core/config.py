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
    # Future AWS fields — add when AWS is set up:
    # AWS_REGION: str = "us-east-1"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Module-level settings instance
settings = Settings()
