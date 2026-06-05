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

    # OpenAI — chat LLM (LLM_PROVIDER=openai) + voice notes (Whisper STT + TTS).
    OPENAI_API_KEY: str = ""
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    OPENAI_STT_MODEL: str = "whisper-1"
    OPENAI_TTS_MODEL: str = "gpt-4o-mini-tts"
    OPENAI_TTS_VOICE: str = "nova"  # warm voice — fits the maternal-care context

    # Realtime API (live voice conversation) — ephemeral keys minted server-side.
    OPENAI_REALTIME_MODEL: str = "gpt-realtime"
    OPENAI_REALTIME_VOICE: str = "marin"

    # TURN relay for doctor->patient WebRTC calls — coturn on this VPS.
    # TURN_SECRET must match static-auth-secret in /etc/turnserver.conf;
    # ice-config mints time-limited HMAC credentials from it (RFC TURN REST).
    TURN_HOST: str = ""    # e.g. 81.17.100.244
    TURN_SECRET: str = ""
    # Legacy static-credential fallback (unused when TURN_SECRET is set)
    TURN_URL: str = ""
    TURN_USERNAME: str = ""
    TURN_CREDENTIAL: str = ""

    # Twilio SMS (choronko track).
    # Add these to .env:
    #   TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    #   TWILIO_AUTH_TOKEN=your_auth_token
    #   TWILIO_FROM_NUMBER=+1xxxxxxxxxx   (must be SMS-capable for +237 Cameroon)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""

    # OneSignal push (smartphone track).
    # Add these to .env:
    #   ONESIGNAL_APP_ID=your_app_id
    #   ONESIGNAL_REST_API_KEY=os_v2_app_...
    # Flutter app must call OneSignal.login(<patient_uuid>) at sign-in so the
    # device is addressable by the same UUID the backend uses.
    ONESIGNAL_APP_ID: str = ""
    ONESIGNAL_REST_API_KEY: str = ""

    # Default region for parsing phone numbers without a leading "+" (E.164).
    # "CM" = Cameroon. Numbers already in +E.164 ignore this.
    DEFAULT_PHONE_REGION: str = "CM"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
