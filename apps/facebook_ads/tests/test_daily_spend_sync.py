"""Regression tests for the per-account daily-spend sync window.

Bug được sửa: nhánh recurring dùng date_preset="last_7d" — preset này của Facebook
KHÔNG bao gồm ngày hôm nay, nên cron mỗi phút không bao giờ cập nhật chi tiêu hôm nay
(buộc nhân viên phải bấm "Đồng bộ chi tiêu" tay). Fix: dùng time_range since/until
kết thúc ở hôm nay để luôn gồm hôm nay.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.facebook_ads import services
from apps.facebook_ads.models import (
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookApp,
)


class SyncDailySpendWindowTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
            status=FacebookAdAccount.STATUS_ACTIVE,
        )
        self.today = timezone.localdate()

    def _today_row(self):
        return {
            "date_start": self.today.isoformat(),
            "spend": "100.00",
            "impressions": "10",
            "clicks": "2",
            "reach": "8",
        }

    def test_recurring_account_refreshes_window_including_today(self):
        # Account "recurring": đã có ít nhất 1 dòng chi tiêu trước đó.
        FacebookAdAccountDailySpend.objects.create(
            ad_account=self.account,
            date=self.today - timedelta(days=3),
            spend=50,
        )

        with patch.object(services, "fetch_insights_batch") as mock_fetch:
            mock_fetch.return_value = {self.account.id: [self._today_row()]}
            services.sync_daily_spend_for_app(self.app)

        # Đúng 1 lần gọi cho nhánh recurring, và phải dùng since/until — KHÔNG dùng preset.
        self.assertEqual(mock_fetch.call_count, 1)
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs.get("until"), self.today.isoformat())
        self.assertEqual(
            kwargs.get("since"), (self.today - timedelta(days=services.RECENT_DAYS)).isoformat()
        )
        self.assertIsNone(kwargs.get("date_preset"))

        # Chi tiêu hôm nay đã được ghi vào DB (đây là điều cron trước đây bỏ sót).
        row = FacebookAdAccountDailySpend.objects.get(ad_account=self.account, date=self.today)
        self.assertEqual(str(row.spend), "100.00")

    def test_fresh_account_backfills_full_year(self):
        # Account chưa có dữ liệu → backfill 365 ngày, until = hôm nay.
        with patch.object(services, "fetch_insights_batch") as mock_fetch:
            mock_fetch.return_value = {self.account.id: []}
            services.sync_daily_spend_for_app(self.app)

        self.assertEqual(mock_fetch.call_count, 1)
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs.get("until"), self.today.isoformat())
        self.assertEqual(kwargs.get("since"), (self.today - timedelta(days=365)).isoformat())
        self.assertIsNone(kwargs.get("date_preset"))
