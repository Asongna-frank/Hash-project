# app/services/voice_service.py
"""
Voice layer for the chat — OpenAI Whisper (speech-to-text) + TTS (text-to-speech).

Used by POST /chat/voice: the patient sends a voice note, we transcribe it,
run it through the SAME channel-agnostic brain as typed messages (triage,
red flags, loss detection, alerting all apply — a spoken "I am bleeding"
raises the same hospital alert), then synthesize the reply as warm audio.

Endpoints used (https://developers.openai.com/api/docs):
  STT: POST /v1/audio/transcriptions   (multipart: file, model[, language])
  TTS: POST /v1/audio/speech           (json: model, input, voice[, instructions])

Both calls are fail-soft at the caller: STT failure → 422 with a clear message;
TTS failure → text-only reply (audio_base64 = null) — the care never blocks
on the voice cosmetics.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_STT_URL = "https://api.openai.com/v1/audio/transcriptions"
_TTS_URL = "https://api.openai.com/v1/audio/speech"

# Whisper-supported containers (25 MB limit per the docs)
SUPPORTED_AUDIO_TYPES = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Tone prompt for gpt-4o-mini-tts (ignored by tts-1 models).
_TTS_INSTRUCTIONS = (
    "You are a Cameroonian woman, a maternal nurse, speaking naturally to a "
    "patient she knows. Gentle African (Cameroonian) accent, normal everyday "
    "conversational delivery — calm, kind and natural, like a real person "
    "talking, not performing. No theatrical warmth, no exaggerated sweetness. "
    "In French, a natural Cameroonian French accent."
)


class VoiceServiceError(Exception):
    """Raised when transcription fails (caller maps to an HTTP error)."""


def transcribe_audio(audio_bytes: bytes, filename: str, language: str | None = None) -> str:
    """
    Transcribe a voice note to text via Whisper.
    `language` is an optional ISO-639-1 hint (e.g. "fr") from the patient
    profile — improves accuracy but auto-detection works without it.
    """
    if not settings.OPENAI_API_KEY:
        raise VoiceServiceError("Voice is not configured on the server")

    data = {"model": settings.OPENAI_STT_MODEL}
    if language and len(language) == 2:
        data["language"] = language.lower()

    try:
        resp = httpx.post(
            _STT_URL,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            data=data,
            files={"file": (filename, audio_bytes)},
            timeout=60.0,
        )
        resp.raise_for_status()
        text = (resp.json().get("text") or "").strip()
    except httpx.HTTPStatusError as exc:
        logger.error("Whisper STT failed | status=%d | %s",
                     exc.response.status_code, exc.response.text[:300])
        raise VoiceServiceError("Could not understand the audio — please try again")
    except Exception as exc:  # noqa: BLE001
        logger.error("Whisper STT error: %s", exc)
        raise VoiceServiceError("Voice transcription is temporarily unavailable")

    if not text:
        raise VoiceServiceError("No speech detected in the audio")
    return text


def synthesize_speech(text: str) -> bytes | None:
    """
    Convert a reply to warm MP3 audio. Returns None on any failure —
    the caller falls back to text-only (voice is enhancement, not care).
    """
    if not settings.OPENAI_API_KEY or not text.strip():
        return None

    payload = {
        "model": settings.OPENAI_TTS_MODEL,
        "voice": settings.OPENAI_TTS_VOICE,
        "input": text[:4000],  # API input limit safety
        "response_format": "mp3",
    }
    # `instructions` is only supported by gpt-4o-mini-tts
    if settings.OPENAI_TTS_MODEL.startswith("gpt-"):
        payload["instructions"] = _TTS_INSTRUCTIONS

    try:
        resp = httpx.post(
            _TTS_URL,
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.error("TTS failed (sending text-only reply): %s", exc)
        return None
