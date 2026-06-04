# app/services/sms_service.py
"""
SMS provider abstraction — Twilio implementation.

All SMS sends go through the module-level `sms_service` singleton.
Never import twilio.rest.Client directly from a router or business-logic file.

TRIAL-ACCOUNT NOTE (leave for operators):
  Twilio trial accounts can only send to numbers verified in the Twilio Console
  (Verified Caller IDs). Messages will carry a trial prefix. The FROM number
  (+TWILIO_FROM_NUMBER) must be SMS-capable for the destination country (+237
  Cameroon). These are not code bugs — they are trial account restrictions.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SMSResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None


class BaseSMSService(ABC):
    @abstractmethod
    def send_sms(self, to: str, message: str) -> SMSResult: ...


class TwilioSMSService(BaseSMSService):
    """
    Send SMS via Twilio's Messages API.

    `to` must be E.164 (e.g. +2376xxxxxxxx). Ensure patient phones are stored
    and normalised to E.164 format before passing them here.
    """

    def __init__(self) -> None:
        try:
            from twilio.rest import Client
            self._client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        except Exception as exc:
            # Missing or malformed credentials — the service will no-op rather than
            # prevent app startup.  Real errors surface on the first send attempt.
            logger.warning("TwilioSMSService init warning: %s", exc)
            self._client = None
        self._from = settings.TWILIO_FROM_NUMBER

    def send_sms(self, to: str, message: str) -> SMSResult:
        if not self._client:
            logger.error("Twilio SMS not configured — cannot send to %s", to)
            return SMSResult(ok=False, error="Twilio not configured")

        try:
            from twilio.base.exceptions import TwilioRestException
            msg = self._client.messages.create(
                body=message, from_=self._from, to=to
            )
            logger.info(
                "Twilio SMS sent | to=%s | sid=%s | status=%s",
                to, msg.sid, msg.status,
            )
            return SMSResult(ok=True, provider_message_id=msg.sid)
        except Exception as exc:
            # Catch TwilioRestException (code + msg attributes) and any other error.
            code = getattr(exc, "code", None)
            detail = getattr(exc, "msg", None) or str(exc)
            logger.error(
                "Twilio SMS failed | to=%s | code=%s | %s", to, code, detail
            )
            return SMSResult(ok=False, error=f"{code}: {detail}" if code else detail)


def get_sms_service() -> BaseSMSService:
    return TwilioSMSService()


sms_service: BaseSMSService = get_sms_service()  # import this everywhere


# ── INBOUND SMS (two-way) ─────────────────────────────────────────────────────
# Twilio cannot do two-way SMS in Cameroon, so the inbound provider is UNDECIDED.
# The SMS layer is therefore two-directional and provider-split: outbound stays
# Twilio (above); inbound is a separate, provider-neutral abstraction so the
# eventual receive provider (e.g. Africa's Talking) can differ from the send one.
# Only the concrete parser is stubbed — the abstraction and webhook flow are real.

@dataclass
class InboundSMS:
    from_phone: str                    # raw sender; normalize to E.164 before lookup
    text: str
    provider_message_id: str | None    # used for inbound idempotency


class BaseInboundSMSParser(ABC):
    @abstractmethod
    def verify_and_parse(self, request_headers: dict, raw_body: bytes) -> InboundSMS:
        """
        Validate the provider's webhook signature and parse its payload into an
        InboundSMS. Must raise on an invalid/unverified signature.
        """
        ...


class StubInboundSMSParser(BaseInboundSMSParser):
    """
    Placeholder until the inbound SMS provider is chosen. Everything around it
    (webhook endpoint, patient lookup, idempotency, brain dispatch, reply send)
    is built and tested — only this adapter is missing. Provider-specific details
    still to wire: signature header name, payload field names, and the
    receiving number/shortcode.
    """

    def verify_and_parse(self, request_headers: dict, raw_body: bytes) -> InboundSMS:
        raise NotImplementedError("Inbound SMS provider not yet selected")


def get_inbound_sms_parser() -> BaseInboundSMSParser:
    return StubInboundSMSParser()


inbound_sms_parser: BaseInboundSMSParser = get_inbound_sms_parser()
