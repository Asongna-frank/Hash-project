# app/services/sms_service.py
"""
SMS provider abstraction — Twilio + Africa's Talking implementations.

The active outbound provider is chosen by settings.SMS_PROVIDER:
  "africastalking" → AfricasTalkingSMSService (two-way for Cameroon; ACTIVE)
  "twilio"         → TwilioSMSService (outbound only; kept for fallback/other markets)

All SMS sends go through the module-level `sms_service` singleton — so flipping
SMS_PROVIDER switches every caller (chat replies, tips, check-ins, appointment
reminders, post-loss, hospital alerts) at once. Never import a provider client
(twilio.rest.Client, etc.) directly from a router or business-logic file.

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


class AfricasTalkingSMSService(BaseSMSService):
    """
    Send SMS via Africa's Talking — the two-way provider for Cameroon.

    `to` must be E.164 (e.g. +2376xxxxxxxx). The sender is the registered
    shortcode (settings.AFRICASTALKING_SHORTCODE); in the sandbox it may be
    omitted, in which case AT delivers only to the Sandbox simulator.

    Called directly over httpx (no SDK dependency), mirroring the project's
    OpenAI / OneSignal integrations.
    """

    def __init__(self) -> None:
        self._username = settings.AFRICASTALKING_USERNAME
        self._api_key = settings.AFRICASTALKING_API_KEY
        self._sender = settings.AFRICASTALKING_SHORTCODE or None
        # Sandbox uses the literal username "sandbox" and a separate API host.
        base = (
            "https://api.sandbox.africastalking.com"
            if self._username == "sandbox"
            else "https://api.africastalking.com"
        )
        self._url = f"{base}/version1/messaging"

    def send_sms(self, to: str, message: str) -> SMSResult:
        if not self._username or not self._api_key:
            logger.error("Africa's Talking SMS not configured — cannot send to %s", to)
            return SMSResult(ok=False, error="Africa's Talking not configured")

        data = {"username": self._username, "to": to, "message": message}
        if self._sender:
            data["from"] = self._sender
        headers = {
            "apiKey": self._api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        try:
            import httpx

            resp = httpx.post(self._url, data=data, headers=headers, timeout=15.0)
            resp.raise_for_status()
            body = resp.json()
            recipients = body.get("SMSMessageData", {}).get("Recipients", [])
            if recipients:
                r0 = recipients[0]
                # AT success codes: 100 Processed, 101 Sent, 102 Queued.
                if r0.get("status") == "Success" or str(r0.get("statusCode")) in ("100", "101", "102"):
                    logger.info(
                        "AfricasTalking SMS sent | to=%s | id=%s | status=%s",
                        to, r0.get("messageId"), r0.get("status"),
                    )
                    return SMSResult(ok=True, provider_message_id=r0.get("messageId"))
                return SMSResult(ok=False, error=f"{r0.get('statusCode')}: {r0.get('status')}")

            # No recipients usually means an invalid/unregistered sender or number.
            detail = body.get("SMSMessageData", {}).get("Message", "no recipients accepted")
            logger.error("AfricasTalking SMS rejected | to=%s | %s", to, detail)
            return SMSResult(ok=False, error=detail)
        except Exception as exc:  # noqa: BLE001 — network/HTTP/JSON errors
            detail = str(exc)
            logger.error("AfricasTalking SMS failed | to=%s | %s", to, detail)
            return SMSResult(ok=False, error=detail)


def get_sms_service() -> BaseSMSService:
    if settings.SMS_PROVIDER == "africastalking":
        return AfricasTalkingSMSService()
    return TwilioSMSService()


sms_service: BaseSMSService = get_sms_service()  # import this everywhere


# ── INBOUND SMS (two-way) ─────────────────────────────────────────────────────
# Twilio cannot do two-way SMS in Cameroon, so receiving is handled by a separate,
# provider-neutral abstraction. Africa's Talking is the chosen two-way provider
# (send + receive via a registered shortcode); set SMS_PROVIDER=africastalking to
# route both directions through it. The Twilio inbound parser remains available
# for environments where a Twilio number is used.

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


class TwilioInboundSMSParser(BaseInboundSMSParser):
    """
    Inbound SMS via a Twilio number's incoming-message webhook.

    Twilio POSTs application/x-www-form-urlencoded fields (From, Body,
    MessageSid, ...) and signs every request with X-Twilio-Signature
    (HMAC-SHA1 over the exact public URL + sorted params, keyed by the
    auth token). We verify with Twilio's own RequestValidator — a request
    that fails validation raises and the webhook returns 400.
    """

    def __init__(self) -> None:
        self._auth_token = settings.TWILIO_AUTH_TOKEN
        self._url = settings.TWILIO_INBOUND_WEBHOOK_URL

    def verify_and_parse(self, request_headers: dict, raw_body: bytes) -> InboundSMS:
        from urllib.parse import parse_qsl

        from twilio.request_validator import RequestValidator

        params = dict(parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True))
        signature = request_headers.get("x-twilio-signature", "")
        if not RequestValidator(self._auth_token).validate(self._url, params, signature):
            raise ValueError("Invalid Twilio signature")

        from_phone = params.get("From", "")
        text = params.get("Body", "")
        if not from_phone or not text.strip():
            raise ValueError("Missing From/Body in Twilio payload")
        return InboundSMS(
            from_phone=from_phone,
            text=text,
            provider_message_id=params.get("MessageSid"),
        )


class AfricasTalkingInboundSMSParser(BaseInboundSMSParser):
    """
    Inbound SMS via an Africa's Talking shortcode "Incoming Messages" callback.

    AT POSTs application/x-www-form-urlencoded fields: from, to, text, id,
    linkId, date. There is NO request signature, so authenticity cannot be
    cryptographically verified here. Two production hardening options (out of
    scope for the sandbox): (1) register the callback on a secret, unguessable
    path, and/or (2) IP-allowlist Africa's Talking's egress ranges at the proxy.

    `id` is AT's unique message id and feeds the webhook's idempotency guard.
    """

    def __init__(self) -> None:
        # Optional: the shortcode we expect in the `to` field. When set, messages
        # addressed to a different shortcode are rejected.
        self._expected_shortcode = settings.AFRICASTALKING_SHORTCODE or None

    def verify_and_parse(self, request_headers: dict, raw_body: bytes) -> InboundSMS:
        from urllib.parse import parse_qsl

        params = dict(parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True))

        from_phone = params.get("from", "")
        text = params.get("text", "")
        if not from_phone or not text.strip():
            raise ValueError("Missing from/text in Africa's Talking payload")

        if self._expected_shortcode and params.get("to") and params["to"] != self._expected_shortcode:
            raise ValueError("Inbound SMS addressed to an unexpected shortcode")

        return InboundSMS(
            from_phone=from_phone,
            text=text,
            provider_message_id=params.get("id"),
        )


class StubInboundSMSParser(BaseInboundSMSParser):
    """
    Placeholder used only when no inbound provider is configured. Everything
    around it (webhook endpoint, patient lookup, idempotency, brain dispatch,
    reply send) is built and tested — selecting a provider activates the channel.
    """

    def verify_and_parse(self, request_headers: dict, raw_body: bytes) -> InboundSMS:
        raise NotImplementedError("Inbound SMS provider not yet selected")


def get_inbound_sms_parser() -> BaseInboundSMSParser:
    if settings.SMS_PROVIDER == "africastalking":
        return AfricasTalkingInboundSMSParser()
    if settings.TWILIO_AUTH_TOKEN and settings.TWILIO_INBOUND_WEBHOOK_URL:
        return TwilioInboundSMSParser()
    return StubInboundSMSParser()


inbound_sms_parser: BaseInboundSMSParser = get_inbound_sms_parser()
