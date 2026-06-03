# app/services/sms_service.py
"""
SMS provider abstraction for HASH.

Queen SMS is the only implementation for the MVP. Swapping to Amazon SNS or
another provider later is a new subclass + a settings change — no caller code
changes. Never call the Queen SMS HTTP endpoint directly from a router or
business-logic file; always go through the module-level `sms_service`.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SMSResult:
    ok: bool
    provider_message_id: str | None = None
    raw: dict | None = None
    error: str | None = None


class BaseSMSService(ABC):
    @abstractmethod
    def send_sms(self, to: str, message: str) -> SMSResult:
        """Send a single SMS to one recipient phone number."""
        ...


class QueenSMSService(BaseSMSService):
    def __init__(self) -> None:
        self._api_key = settings.QUEEN_SMS_API_KEY
        self._sender_id = settings.QUEEN_SMS_SENDER_ID
        self._base_url = settings.QUEEN_SMS_BASE_URL.rstrip("/")

    def send_sms(self, to: str, message: str) -> SMSResult:
        url = f"{self._base_url}/sms.php"
        payload = {
            "api_key": self._api_key,
            "senderid": self._sender_id,   # max 11 chars
            "sms": message,
            "mobiles": to,                 # "237" prefix optional; comma-sep for many
        }
        try:
            resp = httpx.post(url, data=payload, timeout=15.0)  # x-www-form-urlencoded
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # network / non-2xx / bad JSON
            logger.error("Queen SMS request failed | to=%s | error=%s", to, exc)
            return SMSResult(ok=False, error=str(exc))

        if data.get("responsecode") == 1:
            msg_id = None
            sms_list = data.get("sms") or []
            if sms_list:
                msg_id = sms_list[0].get("messageid")
            logger.info("Queen SMS sent | to=%s | messageid=%s", to, msg_id)
            return SMSResult(ok=True, provider_message_id=msg_id, raw=data)

        err = data.get("errordescription") or data.get("responsedescription") or "unknown"
        logger.error("Queen SMS error | to=%s | resp=%s", to, data)
        return SMSResult(ok=False, raw=data, error=str(err))


def get_sms_service() -> BaseSMSService:
    return QueenSMSService()


sms_service = get_sms_service()  # module-level singleton — import this everywhere
