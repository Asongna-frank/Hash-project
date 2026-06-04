# app/services/push_service.py
"""
Push notification provider abstraction — OneSignal implementation.

Targets patients by their UUID, which is the same ID the Flutter app
registers via OneSignal.login(<patient_uuid>) at sign-in. Without that
Flutter call, notifications are accepted by OneSignal but reach zero
devices — a clean API response with recipients=0 is expected until a
subscribed device exists.

All push sends go through the module-level `push_service` singleton.
Push failures are non-fatal: a logged error rather than a raised exception,
so a transient push outage never disrupts the in-app message flow.

OneSignal REST API (current) reference:
  https://documentation.onesignal.com/reference/create-notification
  Endpoint:    https://api.onesignal.com/notifications
  Auth header: Authorization: Key <REST_API_KEY>
               Keys starting with "os_v2_app_" use "Key" prefix (current).
               Legacy keys (32-char hex) need "Basic" instead — check the
               console if you see 401s.
  Targeting:   include_aliases: {"external_id": [...]} + target_channel: "push"
               (replaces deprecated include_external_user_ids)
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_ONESIGNAL_URL = "https://api.onesignal.com/notifications"


@dataclass
class PushResult:
    ok: bool
    recipients: int = 0
    notification_id: str | None = None
    error: str | None = None


class BasePushService(ABC):
    @abstractmethod
    def send_push(self, patient_uuid: str, title: str, message: str) -> PushResult: ...


class OneSignalPushService(BasePushService):
    """
    Send push notification via OneSignal REST API (current).

    `patient_uuid` must be the string representation of the patient's UUID,
    matching what the Flutter app passes to OneSignal.login().
    """

    def __init__(self) -> None:
        self._app_id = settings.ONESIGNAL_APP_ID
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Key {settings.ONESIGNAL_REST_API_KEY}",
        }

    def send_push(self, patient_uuid: str, title: str, message: str) -> PushResult:
        if not self._app_id or not settings.ONESIGNAL_REST_API_KEY:
            logger.error("OneSignal push not configured — skipping push to patient %s", patient_uuid)
            return PushResult(ok=False, error="OneSignal not configured")

        payload = {
            "app_id": self._app_id,
            "target_channel": "push",
            "include_aliases": {"external_id": [patient_uuid]},
            "contents": {"en": message},
            "headings": {"en": title},
        }

        try:
            resp = httpx.post(_ONESIGNAL_URL, json=payload, headers=self._headers, timeout=10.0)
            data = resp.json()

            if resp.status_code == 200 and "id" in data:
                recipients = data.get("recipients", 0)
                logger.info(
                    "OneSignal push sent | patient=%s | notification_id=%s | recipients=%d",
                    patient_uuid, data["id"], recipients,
                )
                return PushResult(ok=True, recipients=recipients, notification_id=data["id"])

            errors = data.get("errors", [resp.text])
            logger.error(
                "OneSignal push rejected | patient=%s | status=%d | errors=%s",
                patient_uuid, resp.status_code, errors,
            )
            return PushResult(ok=False, error=str(errors))

        except Exception as exc:
            logger.error("OneSignal push exception | patient=%s | %s", patient_uuid, exc)
            return PushResult(ok=False, error=str(exc))


def get_push_service() -> BasePushService:
    return OneSignalPushService()


push_service: BasePushService = get_push_service()
