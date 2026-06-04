# app/services/translation_service.py
"""
Translation abstraction for the multilingual pivot.

The chat brain works entirely in English: inbound patient text is translated
TO English before triage/loss-detection, and the English reply is translated
back to the patient's language before sending. This keeps ONE keyword layer and
ONE triage path regardless of language (no per-language care drift).

Provider-neutral, mirroring llm_service / sms_service: all translation goes
through the module-level `translation_service` singleton. The MVP implementation
is LLM-backed (reuses llm_service); swapping to a dedicated translation API later
is one new subclass + one factory change — no caller changes.

CRISIS messages are NEVER translated here — they come pre-approved from
content_store. Static keyword lists stay English-only; the pivot handles language.
"""

import logging
from abc import ABC, abstractmethod

from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

# Human-readable names for the prompt, by ISO code.
_LANG_NAMES = {
    "en": "English",
    "fr": "French",
    "pt": "Portuguese",
}


class BaseTranslationService(ABC):
    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate `text` from source_lang to target_lang. Raise on failure."""
        ...


class LLMTranslationService(BaseTranslationService):
    """LLM-backed translation via the existing llm_service abstraction."""

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        src = (source_lang or "en").lower()
        tgt = (target_lang or "en").lower()
        if src == tgt or not text.strip():
            return text

        src_name = _LANG_NAMES.get(src, src)
        tgt_name = _LANG_NAMES.get(tgt, tgt)
        system_prompt = (
            f"You are a precise translator for a maternal-health service. "
            f"Translate the user's message from {src_name} to {tgt_name}. "
            f"Preserve meaning, tone, and any medical concern faithfully. "
            f"Return ONLY the translated text — no quotes, no preamble, no explanation."
        )
        result = llm_service.classify_message(
            message=text,
            system_prompt=system_prompt,
            max_tokens=400,
            temperature=0.0,
        )
        return result.strip()


def get_translation_service() -> BaseTranslationService:
    return LLMTranslationService()


# Module-level singleton — import this everywhere.
translation_service: BaseTranslationService = get_translation_service()
