"""Task chi tiêu grain ad: ngắt mạch phải khớp hành vi của _sync_all_daily_spend."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.facebook_ads import tasks
from apps.facebook_ads.models import FacebookApp


class SyncAllAdSpendTests(TestCase):
    def setUp(self):
        self.ok_app = FacebookApp.objects.create(
            name="OK", app_id="1", app_secret="s", access_token="t",
        )
        self.dead_app = FacebookApp.objects.create(
            name="Dead", app_id="2", app_secret="s", access_token="t",
            token_status=FacebookApp.TOKEN_INVALID,
        )

    def test_skips_app_with_invalid_token(self):
        """Token chết thì gọi API bao nhiêu lần cũng vô nghĩa — ngắt mạch."""
        with patch.object(tasks, "sync_ad_spend_for_app", return_value={
            "rows_upserted": 3,
        }) as sync:
            summary = tasks._sync_all_ad_spend()

        self.assertEqual(summary["skipped_apps"], 1)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(sync.call_args[0][0].pk, self.ok_app.pk)

    def test_skips_invalid_token_with_throttled_log(self):
        """Bỏ qua app token hỏng phải để lại dấu vết quan sát được (throttled 1
        lần/giờ) — nếu im lặng hoàn toàn thì không ai biết chi tiêu grain ad của
        app này đã ngừng đồng bộ cho tới khi có người tình cờ phát hiện."""
        cache_key = tasks._breaker_log_key("ad_spend", self.dead_app.pk)
        cache.delete(cache_key)

        with patch.object(tasks, "sync_ad_spend_for_app", return_value={
            "rows_upserted": 1,
        }):
            tasks._sync_all_ad_spend()

        self.assertIsNotNone(cache.get(cache_key))

    def test_breaker_log_is_not_swallowed_by_a_sibling_task(self):
        """Ba task cùng đọc trạng thái token của một app. Trước fix, cả ba dùng chung
        khoá `fb:breaker_logged:<pk>` nên task nào chạy trước trong giờ đó chiếm mất
        `cache.add` — hai task còn lại im lặng hoàn toàn và dòng log ngắt mạch (thêm
        vào ở một vòng fix trước) trở thành code chết trên production.
        """
        for scope in ("daily_spend", "ad_catalog", "ad_spend"):
            cache.delete(tasks._breaker_log_key(scope, self.dead_app.pk))

        with patch.object(tasks, "sync_ad_catalog_for_app", return_value={}):
            tasks._sync_all_ad_catalog()
        with patch.object(tasks, "sync_ad_spend_for_app", return_value={}):
            with self.assertLogs("apps.facebook_ads.tasks", level="WARNING") as logs:
                tasks._sync_all_ad_spend()

        self.assertTrue(any("sync_ad_spend: bỏ qua app" in m for m in logs.output))

    def test_summary_reports_failed_accounts_and_variance(self):
        """Kết quả task là thứ operator đọc. Bỏ hai khoá này đi thì task báo "thành
        công sạch" trong khi vài TKQC im lặng hỏng và chốt so khớp 0.98 đã kêu.
        """
        with patch.object(tasks, "sync_ad_spend_for_app", return_value={
            "rows_upserted": 5, "failed_accounts": 2, "variance_findings": 1,
        }):
            with self.assertLogs("apps.facebook_ads.tasks", level="WARNING"):
                summary = tasks._sync_all_ad_spend()

        self.assertEqual(summary["failed_accounts"], 2)
        self.assertEqual(summary["variance_findings"], 1)

    def test_successful_sync_marks_token_ok(self):
        """Sync thành công phải tự đóng lại mạch ngắt — nếu không, app từng gặp lỗi
        tạm thời rồi phục hồi vẫn kẹt ở trạng thái cũ mãi vì không ai xoá cờ đó."""
        self.assertEqual(self.ok_app.token_status, FacebookApp.TOKEN_UNKNOWN)
        with patch.object(tasks, "sync_ad_spend_for_app", return_value={
            "rows_upserted": 1,
        }):
            tasks._sync_all_ad_spend()

        self.ok_app.refresh_from_db()
        self.assertEqual(self.ok_app.token_status, FacebookApp.TOKEN_OK)
