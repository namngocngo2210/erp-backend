"""Hai bề mặt vận hành hiển thị/ghi trạng thái token: view đồng bộ tay + command CLI.

Gộp từ test_sync_view_token_state.py + test_fb_token_status_command.py
(tái cấu trúc 2026-08-05). Docstring gốc của từng file giữ ở docstring class.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.facebook_ads.models import FacebookAdAccount, FacebookApp
from apps.facebook_ads.services import FacebookGraphError, FacebookTokenError
from apps.users.tests.helpers import make_user_with_sections

_VIEWS = "apps.facebook_ads.transport.views.facebook_ads_views_impl.sync_views"


class ManualSyncTokenStateTests(TestCase):
    """Đồng bộ tay cũng phải ghi lại sự thật về token.

    Người vận hành dán token mới rồi bấm "Đồng bộ chi phí" — nếu view không cập nhật
    ``token_status`` thì badge trên màn hình vẫn "Chưa kiểm tra" và ngắt mạch không
    được bật/tắt cho tới lượt cron sau. Xem docs/facebook-token-lifecycle-2026-07-27.md.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user_with_sections("fb_ops", ["facebook-ads"])

    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="tok",
        )
        FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="1", fb_id="act_1",
            status=FacebookAdAccount.STATUS_ACTIVE,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _sync(self):
        return self.client.post(f"/api/facebook-ads/apps/{self.app.pk}/sync-daily-spend", {})

    def test_token_error_marks_app_invalid(self):
        with patch(
            f"{_VIEWS}.sync_daily_spend_for_app",
            side_effect=FacebookTokenError("Session has expired"),
        ):
            response = self._sync()

        self.assertEqual(response.status_code, 502)
        self.app.refresh_from_db()
        self.assertEqual(self.app.token_status, FacebookApp.TOKEN_INVALID)
        self.assertIn("Session has expired", self.app.token_error)

    def test_transient_error_leaves_token_state_alone(self):
        with patch(
            f"{_VIEWS}.sync_daily_spend_for_app",
            side_effect=FacebookGraphError("please reduce the amount of data"),
        ):
            response = self._sync()

        self.assertEqual(response.status_code, 502)
        self.app.refresh_from_db()
        self.assertEqual(self.app.token_status, FacebookApp.TOKEN_UNKNOWN)

    def test_successful_manual_sync_clears_invalid_state(self):
        FacebookApp.objects.filter(pk=self.app.pk).update(
            token_status=FacebookApp.TOKEN_INVALID, token_error="cũ",
        )

        with patch(
            f"{_VIEWS}.sync_daily_spend_for_app",
            return_value={"accounts": 1, "rows_upserted": 2},
        ):
            response = self._sync()

        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.token_status, FacebookApp.TOKEN_OK)
        self.assertEqual(self.app.token_error, "")


class FbTokenStatusCommandTests(TestCase):
    """Command vận hành `fb_token_status`: xem/kiểm tra token mà không cần vào Django admin."""

    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App Chính", app_id="123", app_secret="s", access_token="tok",
        )

    def test_lists_stored_state_without_calling_graph_api(self):
        FacebookApp.objects.filter(pk=self.app.pk).update(
            token_status=FacebookApp.TOKEN_INVALID, token_error="Session has expired",
        )
        out = StringIO()

        with patch("apps.facebook_ads.management.commands.fb_token_status.refresh_token_metadata") as mock_refresh:
            call_command("fb_token_status", stdout=out)

        mock_refresh.assert_not_called()
        output = out.getvalue()
        self.assertIn("App Chính", output)
        self.assertIn("invalid", output)

    def test_check_flag_refreshes_metadata(self):
        def _mark_ok(app):
            FacebookApp.objects.filter(pk=app.pk).update(
                token_status=FacebookApp.TOKEN_OK,
                token_expires_at=None,
                token_checked_at=timezone.now(),
            )
            app.refresh_from_db()
            return app

        out = StringIO()
        with patch(
            "apps.facebook_ads.management.commands.fb_token_status.refresh_token_metadata",
            side_effect=_mark_ok,
        ) as mock_refresh:
            call_command("fb_token_status", "--check", stdout=out)

        mock_refresh.assert_called_once()
        self.assertIn("không hết hạn", out.getvalue())

    def test_extend_flag_runs_exchange(self):
        FacebookApp.objects.filter(pk=self.app.pk).update(
            token_status=FacebookApp.TOKEN_EXPIRING,
            token_expires_at=timezone.now() + timedelta(days=3),
        )
        out = StringIO()

        with patch("apps.facebook_ads.management.commands.fb_token_status.refresh_token_metadata"), \
             patch(
                 "apps.facebook_ads.management.commands.fb_token_status.try_extend_token",
                 return_value=True,
             ) as mock_extend:
            call_command("fb_token_status", "--check", "--extend", stdout=out)

        mock_extend.assert_called_once()
