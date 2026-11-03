"""Chi tiêu grain ad: upsert idempotent, cửa sổ gồm hôm nay, không mất chi tiêu ad lạ."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.facebook_ads.infrastructure import ad_spend_sync
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
)


class SyncAdSpendTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.ad = FacebookAd.objects.create(fb_ad_id="A1", ad_account=self.account)
        self.today = timezone.localdate()

    def _row(self, ad_id="A1", spend="100.00"):
        return {
            "date_start": self.today.isoformat(),
            "ad_id": ad_id,
            "spend": spend,
            "impressions": "10",
            "clicks": "2",
            "reach": "8",
        }

    def test_upserts_rows_for_known_ad(self):
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row()]}):
            result = ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(result["rows_upserted"], 1)
        spend = FacebookAdDailySpend.objects.get(ad=self.ad, date=self.today)
        self.assertEqual(str(spend.spend), "100.00")

    def test_is_idempotent(self):
        for _ in range(2):
            with patch.object(ad_spend_sync, "fetch_insights_batch",
                              return_value={self.account.id: [self._row()]}):
                ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(FacebookAdDailySpend.objects.count(), 1)

    def test_second_run_overwrites_spend(self):
        """FB cập nhật muộn số của ngày cũ — lượt sau phải ghi đè, không cộng dồn."""
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row(spend="100.00")]}):
            ad_spend_sync.sync_ad_spend_for_app(self.app)
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row(spend="150.00")]}):
            ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(
            str(FacebookAdDailySpend.objects.get(ad=self.ad).spend), "150.00",
        )

    def test_lazy_creates_ad_missing_from_catalog(self):
        """Ad đã xoá trên FB vẫn còn chi tiêu — tạo bản ghi page=NULL, không bỏ tiền.

        Phải patch `_client`: `_lazy_fetch_ad` gọi Graph API thật, không chặn thì test
        treo 30s rồi mới rơi vào nhánh except.
        """
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: [self._row(ad_id="GHOST")]}), \
             patch.object(ad_spend_sync._client, "get", side_effect=RuntimeError("no network")):
            result = ad_spend_sync.sync_ad_spend_for_app(self.app)

        ghost = FacebookAd.objects.get(fb_ad_id="GHOST")
        self.assertIsNone(ghost.page_id)
        self.assertEqual(result["lazy_fetched_ads"], 1)
        self.assertEqual(FacebookAdDailySpend.objects.get(ad=ghost).spend, 100)

    def test_dedupes_duplicate_rows_keeping_last_value(self):
        """FB trả trùng (ad_id, date_start) trong cùng 1 response — trước fix,
        bulk_create(update_conflicts=True) ném lỗi Postgres "cannot affect row a
        second time" và làm hỏng cả lượt sync. Dòng ĐỨNG SAU phải thắng.
        """
        rows = [self._row(spend="100.00"), self._row(spend="150.00")]
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: rows}):
            result = ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(result["rows_upserted"], 1)
        self.assertEqual(
            str(FacebookAdDailySpend.objects.get(ad=self.ad, date=self.today).spend),
            "150.00",
        )

    def test_one_account_upsert_failure_does_not_block_others(self):
        """1 account lỗi khi upsert (vd DB xung đột) không được chặn account còn lại
        trong cùng lượt sync — sibling grain account (services.py) đã miễn nhiễm vì
        upsert từng dòng, grain ad dùng bulk_create nên phải tự bọc guard.
        """
        other_account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc2", account_id="998", fb_id="act_998",
        )
        other_ad = FacebookAd.objects.create(fb_ad_id="A2", ad_account=other_account)
        real_upsert = ad_spend_sync._upsert_ad_rows

        def _boom_for_first_account(app, account, rows):
            if account.id == self.account.id:
                raise RuntimeError("boom")
            return real_upsert(app, account, rows)

        with patch.object(ad_spend_sync, "fetch_insights_batch", return_value={
            self.account.id: [self._row(ad_id="A1", spend="100.00")],
            other_account.id: [self._row(ad_id="A2", spend="50.00")],
        }), patch.object(ad_spend_sync, "_upsert_ad_rows", side_effect=_boom_for_first_account):
            with self.assertLogs(
                "apps.facebook_ads.infrastructure.ad_spend_sync", level="WARNING",
            ):
                result = ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(result["failed_accounts"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertTrue(
            FacebookAdDailySpend.objects.filter(ad=other_ad, date=self.today).exists(),
        )
        self.assertFalse(
            FacebookAdDailySpend.objects.filter(ad=self.ad, date=self.today).exists(),
        )

    def test_counts_and_logs_rows_missing_ad_id(self):
        """Dòng thiếu ad_id/date_start không ghi được, nhưng KHÔNG được biến mất im
        lặng: cả pipeline này dựng lên với cam kết "chi tiêu luôn để lại dấu vết".
        Nếu FB đổi tên field, đường rơi này nuốt 100% dòng chứ không phải vài dòng.
        """
        rows = [
            self._row(),
            {**self._row(), "ad_id": ""},
            {"ad_id": "A1", "spend": "5.00"},  # thiếu date_start
        ]
        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: rows}):
            with self.assertLogs(
                "apps.facebook_ads.infrastructure.ad_spend_sync", level="WARNING",
            ) as logs:
                result = ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(result["skipped_rows"], 2)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertTrue(any("thiếu ad_id" in message for message in logs.output))

    def test_no_active_account_returns_same_summary_shape(self):
        """Nhánh early-return phải cùng shape với nhánh thường — caller đọc bằng
        .get() nên khoá thiếu âm thầm thành 0 và che mất khác biệt."""
        FacebookAdAccount.objects.filter(app=self.app).update(
            status=FacebookAdAccount.STATUS_REMOVED,
        )
        empty = ad_spend_sync.sync_ad_spend_for_app(self.app)

        with patch.object(ad_spend_sync, "fetch_insights_batch",
                          return_value={self.account.id: []}):
            FacebookAdAccount.objects.filter(app=self.app).update(
                status=FacebookAdAccount.STATUS_ACTIVE,
            )
            normal = ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(set(empty), set(normal))

    def test_recurring_window_includes_today(self):
        """Bài học services.py:383 — preset last_7d của FB KHÔNG gồm hôm nay."""
        captured = {}

        def _capture(app, accounts, **kwargs):
            captured.update(kwargs)
            return {self.account.id: []}

        with patch.object(ad_spend_sync, "fetch_insights_batch", side_effect=_capture):
            ad_spend_sync.sync_ad_spend_for_app(self.app)

        self.assertEqual(captured["until"], self.today.isoformat())
        self.assertEqual(
            captured["since"],
            (self.today - timedelta(days=ad_spend_sync.AD_SPEND_RECENT_DAYS)).isoformat(),
        )
        self.assertEqual(captured["level"], "ad")
