# app/services/vision_service.py
"""
Image understanding for the chat — OpenAI vision (gpt-4o-mini).

Used by POST /chat/image: the patient sends a photo (medication box, hospital
document / ultrasound report, a meal, a visible symptom) with an optional
caption. The vision model answers WITHIN the HASH care rules and returns a
structured result; the caller then runs the deterministic red-flag layer on
the summary+caption and stores both sides in the conversation history, so
image messages live in the same safety net as text and voice.

Hard rules enforced in the prompt: never prescribe or dose, never diagnose,
explain plainly, danger signs → go to hospital now, conservative triage.
"""

import base64
import json
import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_URL = "https://api.openai.com/v1/chat/completions"

SUPPORTED_IMAGE_TYPES = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "gif": "image/gif"}


class VisionServiceError(Exception):
    """Raised when image analysis fails (caller maps to an HTTP error)."""


def _system_prompt(patient_name: str, weeks: int | None, language: str, post_loss: bool) -> str:
    lang_name = {"en": "English", "fr": "French", "pt": "Portuguese"}.get(language, "English")
    who = ("a woman receiving post-pregnancy-loss support" if post_loss
           else f"a pregnant woman{f' at about {weeks} weeks' if weeks is not None else ''}")
    return f"""You are HASH, a warm maternal-care assistant in Cameroon. {patient_name}, {who}, has sent you a PHOTO (with an optional caption).

What the photo may be: a medication package, a hospital document / lab result / ultrasound report, a meal, or a visible symptom (swelling, rash, etc.).

Your job:
1. Understand what the image shows.
2. Reply helpfully in {lang_name}, in plain warm language, 2-5 sentences.
   - Document/report: explain what it says in simple words.
   - Medication photo: say what the medicine is generally used for, and ALWAYS tell her to follow her clinician's/pharmacist's instructions. NEVER state a dose or tell her to take it.
   - Meal: friendly nutrition feedback for pregnancy (iron, folate, protein).
   - Possible symptom: be careful and conservative; if it could be a danger sign (significant swelling, bleeding, infection), tell her clearly to go to her hospital now.
3. Triage the image+caption: "low" (informational), "medium" (worth clinician awareness), "high" (danger sign visible or described).

HARD RULES: never prescribe, never give doses, never diagnose, no alarmist language, prefer over-triage to under-triage. If the image is unclear or not health-related, say so kindly and ask what she wanted to show.

OUTPUT: a single JSON object only — first character {{ and last }}:
{{"summary": "<one-line English description of what the image shows>", "reply": "<your reply to her in {lang_name}>", "triage_level": "low|medium|high"}}"""


def analyze_image(
    image_bytes: bytes,
    ext: str,
    caption: str | None,
    *,
    patient_name: str,
    weeks: int | None,
    language: str,
    post_loss: bool,
) -> dict:
    """Returns {"summary", "reply", "triage_level"}; raises VisionServiceError."""
    if not settings.OPENAI_API_KEY:
        raise VisionServiceError("Image analysis is not configured on the server")

    data_url = (
        f"data:{_MIME_BY_EXT.get(ext, 'image/jpeg')};base64,"
        + base64.b64encode(image_bytes).decode()
    )
    user_content = [
        {"type": "text",
         "text": caption.strip() if caption and caption.strip() else "(no caption — see image)"},
        {"type": "image_url", "image_url": {"url": data_url, "detail": "auto"}},
    ]

    try:
        resp = httpx.post(
            _URL,
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENAI_CHAT_MODEL,
                "max_tokens": 600,
                "temperature": 0.3,
                "messages": [
                    {"role": "system",
                     "content": _system_prompt(patient_name, weeks, language, post_loss)},
                    {"role": "user", "content": user_content},
                ],
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as exc:
        logger.error("Vision call failed | %d | %s",
                     exc.response.status_code, exc.response.text[:300])
        raise VisionServiceError("Could not analyse the image — please try again")
    except Exception as exc:  # noqa: BLE001
        logger.error("Vision call error: %s", exc)
        raise VisionServiceError("Image analysis is temporarily unavailable")

    # Same hardened parsing as the chat brain: tolerate fences / stray text.
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise VisionServiceError("Could not analyse the image — please try again")
        parsed = json.loads(match.group(0))

    triage = str(parsed.get("triage_level", "medium")).lower()
    if triage not in ("low", "medium", "high"):
        triage = "medium"
    return {
        "summary": str(parsed.get("summary", "Image received")).strip(),
        "reply": str(parsed.get("reply", "")).strip()
                 or "I received your photo. Could you tell me a bit more about what you wanted to show me?",
        "triage_level": triage,
    }
