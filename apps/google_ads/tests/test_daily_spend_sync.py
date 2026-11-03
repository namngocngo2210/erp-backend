"""Cách ly lỗi theo từng tài khoản khi kéo chi phí.

Trước fix, một tài khoản lỗi (CUSTOMER_NOT_ENABLED, mất quyền...) làm hỏng cả lần
chạy: mọi tài khoản đứng sau nó không được đồng bộ và không ai biết. Với 5 tài khoản
thì hiếm gặp; quét cả cây MCC lên vài chục tài khoản thì gần như chắc chắn dính.
"""

from unittest.mock import patch

from django.test import TestCase

from apps.google_ads import services
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsAccountDailySpend,
    GoogleAdsConfig,
)


class DailySpendErrorIsolationTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.bad = GoogleAdsAccount.objects.create(
            config=self.config, name="Hỏng", customer_id="3000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        self.good = GoogleAdsAccount.objects.create(
            config=self.config, name="Tốt", customer_id="4000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )

    def test_failing_account_does_not_block_the_next_one(self):
        def fake_fetch(client, customer_id, since, until):
            if customer_id == "3000000000":
                raise services.GoogleAdsApiError("CUSTOMER_NOT_ENABLED")
            return [{"date": "2026-08-01", "spend": "120.00", "impressions": 10, "clicks": 2}]

        with patch.object(services, "_build_client", return_value=object()), \
             patch.object(services, "_fetch_daily_rows", side_effect=fake_fetch):
            result = services.sync_daily_spend_for_config(self.config)

        self.assertEqual(result["failed_accounts"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertTrue(
            GoogleAdsAccountDailySpend.objects.filter(ad_account=self.good).exists()
        )
        self.assertFalse(
            GoogleAdsAccountDailySpend.objects.filter(ad_account=self.bad).exists()
        )

    def test_explicit_range_also_isolates_errors(self):
        def fake_fetch(client, customer_id, since, until):
            raise services.GoogleAdsApiError("boom")

        with patch.object(services, "_build_client", return_value=object()), \
             patch.object(services, "_fetch_daily_rows", side_effect=fake_fetch):
            result = services.sync_daily_spend_for_config(
                self.config, since="2026-08-01", until="2026-08-07",
            )

        self.assertEqual(result["failed_accounts"], 2)
        self.assertEqual(result["rows_upserted"], 0)


class DailySpendSingleFlightTests(TestCase):
    """Beat chạy 300s đúng bằng soft_time_limit — không có khoá là hai lượt chồng nhau."""

    def test_skips_when_another_run_holds_the_lock(self):
        from apps.google_ads import tasks

        with patch.object(tasks, "single_flight") as mock_lock:
            mock_lock.return_value.__enter__.return_value = False
            result = tasks.sync_all_daily_spend_google()

        self.assertEqual(result, {"skipped": "already_running"})

    def test_runs_when_lock_acquired(self):
        from apps.google_ads import tasks

        GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        with patch.object(tasks, "single_flight") as mock_lock, \
             patch.object(
                 tasks, "sync_daily_spend_for_config",
                 return_value={"rows_upserted": 3, "failed_accounts": 0},
             ):
            mock_lock.return_value.__enter__.return_value = True
            result = tasks.sync_all_daily_spend_google()

        self.assertEqual(result["rows_upserted"], 3)
