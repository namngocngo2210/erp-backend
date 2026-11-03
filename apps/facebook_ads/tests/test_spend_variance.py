"""Chốt so khớp: grain ad hụt nhiều so với grain account thì phải kêu."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.facebook_ads.infrastructure import ad_spend_sync
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookAdDailySpend,
    FacebookApp,
)


class SpendVarianceRatioTests(SimpleTestCase):
    def test_returns_ratio(self):
        self.assertAlmostEqual(
            ad_spend_sync.spend_variance_ratio(Decimal("95"), Decimal("100")), 0.95,
        )

    def test_returns_none_when_account_total_is_zero(self):
        """Không có mẫu số thì không có tỷ lệ — tuyệt đối không chia cho 0."""
        self.assertIsNone(ad_spend_sync.spend_variance_ratio(Decimal("0"), Decimal("0")))


class CheckSpendVarianceTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.ad = FacebookAd.objects.create(fb_ad_id="A1", ad_account=self.account)
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def _seed(self, account_spend: str, ad_spend: str, day=None):
        day = day or self.yesterday
        FacebookAdAccountDailySpend.objects.create(
            ad_account=self.account, date=day, spend=Decimal(account_spend),
        )
        FacebookAdDailySpend.objects.create(ad=self.ad, date=day, spend=Decimal(ad_spend))

    def test_alerts_when_ad_total_falls_short(self):
        self._seed("100.00", "90.00")
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(len(findings), 1)
        alert.assert_called_once()
        self.assertIn("fb:spend_variance", alert.call_args.kwargs["key"])

    def test_silent_when_within_threshold(self):
        self._seed("100.00", "99.00")
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(findings, [])
        alert.assert_not_called()

    def test_alerts_when_ad_grain_has_no_rows_at_all(self):
        """Hình dạng của một lần NGỐN TRỌN sự cố ingest: grain account có chi tiêu,
        grain ad không có dòng nào. Đây chính là kịch bản chốt 0.98 sinh ra để bắt,
        nhưng nhánh `ad_totals.get(key, 0)` (không có khoá) trước đây chưa test nào
        đi qua — code đọc thì đúng, chưa ai chứng minh nó đúng.
        """
        FacebookAdAccountDailySpend.objects.create(
            ad_account=self.account, date=self.yesterday, spend=Decimal("100.00"),
        )
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["ratio"], 0.0)
        alert.assert_called_once()

    def test_exactly_at_threshold_is_silent(self):
        """Chốt đúng biên `>= 0.98`: 98.00/100.00 = 0.98 KHÔNG được kêu.

        Test "im lặng" hiện có dùng 0.99 nên biên tự nó chưa bị ghim — đổi lén
        toán tử thành `>` sẽ sinh cảnh báo rác mỗi ngày mà không test nào đỏ.
        """
        self._seed("100.00", "98.00")
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(findings, [])
        alert.assert_not_called()

    def test_just_below_threshold_alerts(self):
        """Ngay dưới biên thì phải kêu — cặp với test trên để khoá cả hai phía."""
        self._seed("100.00", "97.99")
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(len(findings), 1)
        alert.assert_called_once()

    def test_ignores_today(self):
        """Hai grain có độ trễ khác nhau nên hôm nay luôn lệch — kiểm là sinh rác."""
        self._seed("100.00", "10.00", day=timezone.localdate())
        with patch.object(ad_spend_sync, "send_ops_alert") as alert:
            findings = ad_spend_sync.check_spend_variance(self.app)

        self.assertEqual(findings, [])
        alert.assert_not_called()
