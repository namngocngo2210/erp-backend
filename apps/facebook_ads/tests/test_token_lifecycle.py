"""Vòng đời token Facebook App: debug_token, phân loại trạng thái, gia hạn, reset.

Sự cố gốc (25–27/07/2026): token user hết hạn, hệ thống không biết hạn, không cảnh
báo, cron log 1.440 dòng/ngày và chi tiêu quảng cáo đứng im 2 ngày. Xem
docs/facebook-token-lifecycle-2026-07-27.md.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.facebook_ads.infrastructure import token_service
from apps.facebook_ads.models import FacebookApp
from apps.facebook_ads.tests._fakes import _FakeResponse


def _debug_payload(**data) -> dict:
    base = {
        "app_id": "123",
        "type": "USER",
        "application": "App",
        "is_valid": True,
        "scopes": ["ads_read"],
    }
    base.update(data)
    return {"data": base}


class RefreshTokenMetadataTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="secret", access_token="tok",
        )

    def test_system_user_token_is_ok_and_never_expires(self):
        # FB trả expires_at = 0 cho System User token (không hết hạn).
        response = _FakeResponse(200, _debug_payload(type="SYSTEM_USER", expires_at=0))

        with patch.object(token_service._client, "get", return_value=response):
            token_service.refresh_token_metadata(self.app)

        self.app.refresh_from_db()
        self.assertEqual(self.app.token_status, FacebookApp.TOKEN_OK)
        self.assertIsNone(self.app.token_expires_at)
        self.assertIsNotNone(self.app.token_checked_at)
        self.assertEqual(self.app.token_error, "")

    def test_expired_token_is_marked_invalid_with_error_and_since(self):
        response = _FakeResponse(
            200,
            _debug_payload(
                is_valid=False,
                expires_at=int((timezone.now() - timedelta(days=2)).timestamp()),
                error={"code": 190, "message": "Session has expired"},
            ),
        )

        with patch.object(token_service._client, "get", return_value=response):
            token_service.refresh_token_metadata(self.app)

        self.app.refresh_from_db()
        self.assertEqual(self.app.token_status, FacebookApp.TOKEN_INVALID)
        self.assertIn("Session has expired", self.app.token_error)
        self.assertIsNotNone(self.app.token_invalid_since)

    def test_token_expiring_within_warning_window_is_marked_expiring(self):
        expires = timezone.now() + timedelta(days=3)
        response = _FakeResponse(200, _debug_payload(expires_at=int(expires.timestamp())))

        # try_extend_token bị vô hiệu để test riêng bước phân loại trạng thái.
        with patch.object(token_service._client, "get", return_value=response):
            token_service.refresh_token_metadata(self.app)

        self.app.refresh_from_db()
        self.assertEqual(self.app.token_status, FacebookApp.TOKEN_EXPIRING)
        self.assertIsNotNone(self.app.token_expires_at)

    def test_token_valid_far_from_expiry_is_ok(self):
        expires = timezone.now() + timedelta(days=45)
        response = _FakeResponse(200, _debug_payload(expires_at=int(expires.timestamp())))

        with patch.object(token_service._client, "get", return_value=response):
            token_service.refresh_token_metadata(self.app)

        self.app.refresh_from_db()
        self.assertEqual(self.app.token_status, FacebookApp.TOKEN_OK)

    def test_invalid_since_is_preserved_across_repeated_checks(self):
        first_seen = timezone.now() - timedelta(days=2)
        self.app.token_status = FacebookApp.TOKEN_INVALID
        self.app.token_invalid_since = first_seen
        self.app.save(update_fields=["token_status", "token_invalid_since"])

        response = _FakeResponse(200, _debug_payload(is_valid=False))
        with patch.object(token_service._client, "get", return_value=response):
            token_service.refresh_token_metadata(self.app)

        self.app.refresh_from_db()
        # Mốc "hỏng từ bao giờ" phải giữ nguyên, không bị reset mỗi lần cron chạy —
        # nếu không sẽ luôn báo "vừa hỏng" và không ai thấy sự cố kéo dài.
        self.assertEqual(
            self.app.token_invalid_since.replace(microsecond=0),
            first_seen.replace(microsecond=0),
        )

    def test_debug_token_uses_app_access_token_as_inspector(self):
        response = _FakeResponse(200, _debug_payload(expires_at=0))

        with patch.object(token_service._client, "get", return_value=response) as mock_get:
            token_service.debug_token(self.app)

        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        self.assertEqual(params["input_token"], "tok")
        self.assertEqual(params["access_token"], "123|secret")


class TryExtendTokenTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="secret", access_token="short-lived",
        )

    def test_exchange_stores_new_long_lived_token(self):
        exchange = _FakeResponse(
            200, {"access_token": "long-lived", "token_type": "bearer", "expires_in": 5184000},
        )

        with patch.object(token_service._client, "get", return_value=exchange):
            extended = token_service.try_extend_token(self.app)

        self.assertTrue(extended)
        self.app.refresh_from_db()
        self.assertEqual(self.app.access_token, "long-lived")
        self.assertIsNotNone(self.app.token_expires_at)
        self.assertGreater(self.app.token_expires_at, timezone.now() + timedelta(days=55))

    def test_never_expiring_token_is_not_exchanged(self):
        # "Không hết hạn" = ĐÃ soi qua debug_token và FB trả expires_at = 0.
        self.app.token_expires_at = None
        self.app.token_checked_at = timezone.now()
        self.app.token_status = FacebookApp.TOKEN_OK
        self.app.save(update_fields=["token_expires_at", "token_checked_at", "token_status"])

        with patch.object(token_service._client, "get") as mock_get:
            extended = token_service.try_extend_token(self.app)

        self.assertFalse(extended)
        mock_get.assert_not_called()

    def test_already_invalid_token_is_not_exchanged(self):
        # fb_exchange_token yêu cầu token CÒN SỐNG — gọi với token chết chỉ tốn request.
        self.app.token_status = FacebookApp.TOKEN_INVALID
        self.app.token_expires_at = timezone.now() - timedelta(days=1)
        self.app.save(update_fields=["token_status", "token_expires_at"])

        with patch.object(token_service._client, "get") as mock_get:
            extended = token_service.try_extend_token(self.app)

        self.assertFalse(extended)
        mock_get.assert_not_called()


class TokenChangeResetsStatusTests(TestCase):
    def test_saving_a_new_access_token_clears_invalid_state(self):
        app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="secret", access_token="dead",
        )
        app.token_status = FacebookApp.TOKEN_INVALID
        app.token_error = "Session has expired"
        app.token_invalid_since = timezone.now()
        app.token_expires_at = timezone.now() - timedelta(days=1)
        app.save()

        app.access_token = "fresh"
        app.save()

        app.refresh_from_db()
        self.assertEqual(app.token_status, FacebookApp.TOKEN_UNKNOWN)
        self.assertEqual(app.token_error, "")
        self.assertIsNone(app.token_invalid_since)
        self.assertIsNone(app.token_expires_at)

    def test_saving_other_fields_keeps_token_state(self):
        app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="secret", access_token="dead",
        )
        app.token_status = FacebookApp.TOKEN_INVALID
        app.token_error = "Session has expired"
        app.save()

        app.note = "đổi ghi chú thôi"
        app.save()

        app.refresh_from_db()
        self.assertEqual(app.token_status, FacebookApp.TOKEN_INVALID)
        self.assertEqual(app.token_error, "Session has expired")
