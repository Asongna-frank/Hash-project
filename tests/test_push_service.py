# tests/test_push_service.py
"""
Unit tests for OneSignalPushService.

All HTTP calls are mocked — no real network requests, no credentials needed.

Run:  pytest tests/test_push_service.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

from app.services.push_service import OneSignalPushService, PushResult


# ── helpers ───────────────────────────────────────────────────────────────────

PATIENT_UUID = "11111111-2222-3333-4444-555555555555"
TITLE = "HASH — Daily Tip"
MESSAGE = "Stay hydrated and rest when you can."


def _make_service(app_id="test-app-id", rest_key="test-rest-key") -> OneSignalPushService:
    with patch("app.services.push_service.settings") as mock_settings:
        mock_settings.ONESIGNAL_APP_ID = app_id
        mock_settings.ONESIGNAL_REST_API_KEY = rest_key
        svc = OneSignalPushService()
    # Patch instance attributes directly so send_push sees them
    svc._app_id = app_id
    svc._headers = {
        "Content-Type": "application/json",
        "Authorization": f"Key {rest_key}",
    }
    return svc


def _mock_httpx_post(status_code: int, json_body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


# ── happy path ────────────────────────────────────────────────────────────────

class TestSendPushSuccess:
    def test_returns_ok_true_on_200(self):
        svc = _make_service()
        mock_resp = _mock_httpx_post(200, {"id": "notif-abc", "recipients": 1})

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", return_value=mock_resp):
            ms.ONESIGNAL_APP_ID = "test-app-id"
            ms.ONESIGNAL_REST_API_KEY = "test-rest-key"
            result = svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert result.ok is True
        assert result.notification_id == "notif-abc"
        assert result.recipients == 1
        assert result.error is None

    def test_recipients_zero_is_still_ok(self):
        # recipients=0 means credentials are correct, no device registered yet
        svc = _make_service()
        mock_resp = _mock_httpx_post(200, {"id": "notif-xyz", "recipients": 0})

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", return_value=mock_resp):
            ms.ONESIGNAL_APP_ID = "test-app-id"
            ms.ONESIGNAL_REST_API_KEY = "test-rest-key"
            result = svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert result.ok is True
        assert result.recipients == 0
        assert result.notification_id == "notif-xyz"

    def test_correct_payload_sent_to_onesignal(self):
        svc = _make_service()
        mock_resp = _mock_httpx_post(200, {"id": "notif-123", "recipients": 2})
        captured = {}

        def fake_post(url, json, headers, timeout):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return mock_resp

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", side_effect=fake_post):
            ms.ONESIGNAL_APP_ID = "my-app-id"
            ms.ONESIGNAL_REST_API_KEY = "my-rest-key"
            svc._app_id = "my-app-id"
            svc._headers = {"Content-Type": "application/json", "Authorization": "Key my-rest-key"}
            svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert captured["url"] == "https://api.onesignal.com/notifications"
        assert captured["json"]["app_id"] == "my-app-id"
        assert captured["json"]["target_channel"] == "push"
        assert captured["json"]["include_aliases"] == {"external_id": [PATIENT_UUID]}
        assert captured["json"]["contents"]["en"] == MESSAGE
        assert captured["json"]["headings"]["en"] == TITLE

    def test_auth_header_uses_key_prefix(self):
        svc = _make_service(rest_key="os_v2_app_testkey")
        mock_resp = _mock_httpx_post(200, {"id": "n1", "recipients": 1})
        captured = {}

        def fake_post(url, json, headers, timeout):
            captured["headers"] = headers
            return mock_resp

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", side_effect=fake_post):
            ms.ONESIGNAL_APP_ID = "test-app-id"
            ms.ONESIGNAL_REST_API_KEY = "os_v2_app_testkey"
            svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert captured["headers"]["Authorization"] == "Key os_v2_app_testkey"


# ── error paths ───────────────────────────────────────────────────────────────

class TestSendPushFailures:
    def test_returns_not_ok_on_4xx(self):
        svc = _make_service()
        mock_resp = _mock_httpx_post(400, {"errors": ["Invalid app_id"]})

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", return_value=mock_resp):
            ms.ONESIGNAL_APP_ID = "test-app-id"
            ms.ONESIGNAL_REST_API_KEY = "test-rest-key"
            result = svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert result.ok is False
        assert result.error is not None

    def test_returns_not_ok_on_401(self):
        svc = _make_service()
        mock_resp = _mock_httpx_post(401, {"errors": ["Unauthorized"]})

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", return_value=mock_resp):
            ms.ONESIGNAL_APP_ID = "test-app-id"
            ms.ONESIGNAL_REST_API_KEY = "test-rest-key"
            result = svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert result.ok is False

    def test_returns_not_ok_on_network_exception(self):
        svc = _make_service()

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", side_effect=Exception("Connection refused")):
            ms.ONESIGNAL_APP_ID = "test-app-id"
            ms.ONESIGNAL_REST_API_KEY = "test-rest-key"
            result = svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert result.ok is False
        assert "Connection refused" in result.error

    def test_returns_not_ok_when_unconfigured(self):
        svc = _make_service(app_id="", rest_key="")

        with patch("app.services.push_service.settings") as ms:
            ms.ONESIGNAL_APP_ID = ""
            ms.ONESIGNAL_REST_API_KEY = ""
            result = svc.send_push(PATIENT_UUID, TITLE, MESSAGE)

        assert result.ok is False
        assert "not configured" in result.error

    def test_does_not_raise_on_http_error(self):
        # Push failures must never raise — they are non-fatal
        svc = _make_service()

        with patch("app.services.push_service.settings") as ms, \
             patch("app.services.push_service.httpx.post", side_effect=RuntimeError("boom")):
            ms.ONESIGNAL_APP_ID = "test-app-id"
            ms.ONESIGNAL_REST_API_KEY = "test-rest-key"
            result = svc.send_push(PATIENT_UUID, TITLE, MESSAGE)  # must not raise

        assert result.ok is False


# ── PushResult dataclass ──────────────────────────────────────────────────────

class TestPushResult:
    def test_defaults(self):
        r = PushResult(ok=True)
        assert r.recipients == 0
        assert r.notification_id is None
        assert r.error is None

    def test_failure_defaults(self):
        r = PushResult(ok=False, error="some error")
        assert r.ok is False
        assert r.recipients == 0
