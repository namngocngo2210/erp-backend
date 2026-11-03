"""Ngắt mạch khi token chết + cron kiểm tra token hằng ngày.

Trước fix: cron 60s vẫn gọi Graph API cho app có token chết → 1.440 dòng
`logger.warning`/ngày/app, đốt rate limit, và không ai được gọi.

Gộp thêm test_ads_report_token_guard.py (tái cấu trúc 2026-08-05) — docstring gốc
giữ ở docstring class `AdsReportTokenGuardTests`.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.facebook_ads import services, tasks
from apps.facebook_ads.infrastructure import lark_ads_report_sync as sync_mod
from apps.facebook_ads.models import FacebookAdAccount, FacebookApp

_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fb-circuit-breaker-tests",
    }
}

# LocMemCache cùng LOCATION là share chung 1 dict toàn process — guard test phải
# pin LOCATION KHÁC với _LOCMEM ở trên để hai cụm không dẫm state cache của nhau.
_GUARD_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fb-report-guard-tests",
    }
}


@override_settings(CACHES=_LOCMEM)
class SyncCircuitBreakerTests(TestCase):
    def setUp(self):
        self.healthy = FacebookApp.objects.create(
            name="Healthy", app_id="1", app_secret="s", access_token="ok",
        )
        self.broken = FacebookApp.objects.create(
            name="Broken", app_id="2", app_secret="s", access_token="dead",
        )
        FacebookApp.objects.filter(pk=self.broken.pk).update(
            token_status=FacebookApp.TOKEN_INVALID,
            token_invalid_since=timezone.now() - timedelta(days=2),
            token_error="Session has expired",
        )
        for app in (self.healthy, self.broken):
            FacebookAdAccount.objects.create(
                app=app, name=f"Acc {app.pk}", account_id=str(app.pk),
                fb_id=f"act_{app.pk}", status=FacebookAdAccount.STATUS_ACTIVE,
            )

    def test_app_with_invalid_token_is_skipped(self):
        with patch.object(tasks, "sync_daily_spend_for_app", return_value={"rows_upserted": 1}) as mock_sync:
            summary = tasks._sync_all_daily_spend(date_preset=None)

        synced_apps = [call.args[0].pk for call in mock_sync.call_args_list]
        self.assertEqual(synced_apps, [self.healthy.pk])
        self.assertEqual(summary["skipped_apps"], 1)

    def test_token_error_marks_app_invalid_and_alerts(self):
        error = services.FacebookTokenError("Batch error: Session has expired")

        with patch.object(tasks, "sync_daily_spend_for_app", side_effect=error):
            with patch("apps.facebook_ads.tasks.send_ops_alert") as mock_alert:
                tasks._sync_all_daily_spend(date_preset=None)

        self.healthy.refresh_from_db()
        self.assertEqual(self.healthy.token_status, FacebookApp.TOKEN_INVALID)
        self.assertIn("Session has expired", self.healthy.token_error)
        self.assertIsNotNone(self.healthy.token_invalid_since)
        mock_alert.assert_called()

    def test_generic_graph_error_does_not_trip_the_breaker(self):
        error = services.FacebookGraphError("Batch error: please reduce the amount of data")

        with patch.object(tasks, "sync_daily_spend_for_app", side_effect=error):
            tasks._sync_all_daily_spend(date_preset=None)

        self.healthy.refresh_from_db()
        # Lỗi tạm thời không được khoá app lại — lượt sau phải thử tiếp.
        self.assertNotEqual(self.healthy.token_status, FacebookApp.TOKEN_INVALID)

    def test_successful_sync_clears_previous_invalid_state(self):
        FacebookApp.objects.filter(pk=self.healthy.pk).update(
            token_status=FacebookApp.TOKEN_INVALID,
            token_error="cũ",
            token_invalid_since=timezone.now() - timedelta(days=1),
        )
        # Bỏ ngắt mạch để mô phỏng lượt chạy ngay sau khi admin dán token mới.
        FacebookApp.objects.filter(pk=self.healthy.pk).update(
            token_status=FacebookApp.TOKEN_UNKNOWN,
        )

        with patch.object(tasks, "sync_daily_spend_for_app", return_value={"rows_upserted": 3}):
            tasks._sync_all_daily_spend(date_preset=None)

        self.healthy.refresh_from_db()
        self.assertEqual(self.healthy.token_status, FacebookApp.TOKEN_OK)
        self.assertEqual(self.healthy.token_error, "")
        self.assertIsNone(self.healthy.token_invalid_since)


@override_settings(CACHES=_LOCMEM, FB_TOKEN_EXPIRY_WARN_DAYS=7)
class CheckFacebookTokensTaskTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="tok",
        )

    def test_alerts_when_token_invalid(self):
        def _mark_invalid(app):
            app.token_status = FacebookApp.TOKEN_INVALID
            app.token_error = "Session has expired"
            app.token_invalid_since = timezone.now()
            app.save()
            return app

        with patch("apps.facebook_ads.tasks.refresh_token_metadata", side_effect=_mark_invalid):
            with patch("apps.facebook_ads.tasks.send_ops_alert") as mock_alert:
                summary = tasks._check_facebook_tokens()

        self.assertEqual(summary["invalid"], 1)
        mock_alert.assert_called_once()

    def test_never_expiring_token_produces_no_alert(self):
        def _mark_ok(app):
            app.token_status = FacebookApp.TOKEN_OK
            app.token_expires_at = None
            app.save()
            return app

        with patch("apps.facebook_ads.tasks.refresh_token_metadata", side_effect=_mark_ok):
            with patch("apps.facebook_ads.tasks.send_ops_alert") as mock_alert:
                summary = tasks._check_facebook_tokens()

        self.assertEqual(summary["ok"], 1)
        mock_alert.assert_not_called()

    def test_expiring_token_is_extended_before_alerting(self):
        def _mark_expiring(app):
            app.token_status = FacebookApp.TOKEN_EXPIRING
            app.token_expires_at = timezone.now() + timedelta(days=3)
            app.save()
            return app

        def _extend(app):
            app.token_status = FacebookApp.TOKEN_OK
            app.token_expires_at = timezone.now() + timedelta(days=60)
            app.save()
            return True

        with patch("apps.facebook_ads.tasks.refresh_token_metadata", side_effect=_mark_expiring):
            with patch("apps.facebook_ads.tasks.try_extend_token", side_effect=_extend) as mock_extend:
                with patch("apps.facebook_ads.tasks.send_ops_alert") as mock_alert:
                    summary = tasks._check_facebook_tokens()

        mock_extend.assert_called_once()
        self.assertEqual(summary["extended"], 1)
        mock_alert.assert_not_called()

    def test_alerts_when_extension_fails(self):
        def _mark_expiring(app):
            app.token_status = FacebookApp.TOKEN_EXPIRING
            app.token_expires_at = timezone.now() + timedelta(days=2)
            app.save()
            return app

        with patch("apps.facebook_ads.tasks.refresh_token_metadata", side_effect=_mark_expiring):
            with patch("apps.facebook_ads.tasks.try_extend_token", return_value=False):
                with patch("apps.facebook_ads.tasks.send_ops_alert") as mock_alert:
                    summary = tasks._check_facebook_tokens()

        self.assertEqual(summary["expiring"], 1)
        mock_alert.assert_called_once()


@override_settings(CACHES=_GUARD_LOCMEM)
class AdsReportTokenGuardTests(TestCase):
    """Báo cáo ads không được đẩy số thiếu sang Lark khi token Facebook đang hỏng.

    Trong sự cố 25–27/07/2026, `sync_ads_report_lark` vẫn chạy mỗi 30 phút và ghi đè
    Bitable bằng chi phí thiếu 2 app → cột "Tỷ lệ CP/DT" đẹp hơn thực tế. Số cũ (stale)
    an toàn hơn số sai, vì luồng zero-out còn ghi thẳng `Chi phí = 0` cho ngày trống.
    """

    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="dead",
        )
        self._config = patch.object(
            sync_mod, "_ads_table_config", return_value=("app_token", "tbl_ch", "tbl_pr"),
        )
        self._config.start()
        self.addCleanup(self._config.stop)

    def _mark_invalid(self):
        FacebookApp.objects.filter(pk=self.app.pk).update(
            token_status=FacebookApp.TOKEN_INVALID, token_error="Session has expired",
        )

    def test_push_skipped_when_a_token_is_invalid(self):
        self._mark_invalid()

        with patch.object(sync_mod, "_upsert_table") as mock_upsert:
            with patch("apps.facebook_ads.infrastructure.lark_ads_report_sync.send_ops_alert") as mock_alert:
                result = sync_mod.sync_ads_report_to_lark()

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "facebook_token_invalid")
        mock_upsert.assert_not_called()
        mock_alert.assert_called_once()

    def test_force_overrides_the_guard(self):
        self._mark_invalid()

        with patch.object(sync_mod, "_aggregate_channel", return_value=[]), \
             patch.object(sync_mod, "_aggregate_product", return_value=[]), \
             patch.object(sync_mod, "_upsert_table", return_value={}) as mock_upsert:
            result = sync_mod.sync_ads_report_to_lark(force=True)

        self.assertNotIn("skipped", result)
        self.assertEqual(mock_upsert.call_count, 2)

    def test_healthy_tokens_do_not_block_the_push(self):
        FacebookApp.objects.filter(pk=self.app.pk).update(token_status=FacebookApp.TOKEN_OK)

        with patch.object(sync_mod, "_aggregate_channel", return_value=[]), \
             patch.object(sync_mod, "_aggregate_product", return_value=[]), \
             patch.object(sync_mod, "_upsert_table", return_value={}) as mock_upsert:
            result = sync_mod.sync_ads_report_to_lark()

        self.assertNotIn("skipped", result)
        self.assertEqual(mock_upsert.call_count, 2)

    def test_soft_deleted_app_with_dead_token_is_ignored(self):
        self._mark_invalid()
        FacebookApp.objects.filter(pk=self.app.pk).update(deleted_at=timezone.now())

        with patch.object(sync_mod, "_aggregate_channel", return_value=[]), \
             patch.object(sync_mod, "_aggregate_product", return_value=[]), \
             patch.object(sync_mod, "_upsert_table", return_value={}):
            result = sync_mod.sync_ads_report_to_lark()

        self.assertNotIn("skipped", result)
