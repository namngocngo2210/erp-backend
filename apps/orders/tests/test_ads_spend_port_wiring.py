"""Wiring test cho port orders → ads apps (Lô 5).

`_repositories_reports` không import model của `facebook_ads` / `google_ads` nữa
mà hỏi qua `apps.orders.contracts.ads_spend_port`; mỗi app quảng cáo đăng ký
provider của mình tại `AppConfig.ready()`.

Test CỐ Ý không import gì từ hai app quảng cáo — chỉ patch bằng đường dẫn chuỗi —
để chính nó không sinh vi phạm phân tầng mới (bài học MR !1166).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.orders.contracts.ads_spend_port import (
    AdsSpendSource,
    ads_spend_provider,
)

_FB_QS = "apps.facebook_ads.models.FacebookAdAccountDailySpend.objects"
_GG_QS = "apps.google_ads.models.GoogleAdsAccountDailySpend.objects"


class AdsSpendPortWiringTests(SimpleTestCase):
    def test_both_providers_registered_at_app_ready(self):
        fb = ads_spend_provider(AdsSpendSource.FACEBOOK)
        gg = ads_spend_provider(AdsSpendSource.GOOGLE)
        self.assertEqual(type(fb).__module__, "apps.facebook_ads.adapters")
        self.assertEqual(type(fb).__name__, "FacebookAdsSpendAdapter")
        self.assertEqual(type(gg).__module__, "apps.google_ads.adapters")
        self.assertEqual(type(gg).__name__, "GoogleAdsSpendAdapter")

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            ads_spend_provider("tiktok")

    def test_empty_account_ids_short_circuits_without_query(self):
        """Không có tài khoản nào → trả rỗng, KHÔNG chạm DB (giữ nguyên hành vi cũ)."""
        for source, path in (
            (AdsSpendSource.FACEBOOK, _FB_QS),
            (AdsSpendSource.GOOGLE, _GG_QS),
        ):
            with self.subTest(source=source), patch(path) as mock_objects:
                rows = ads_spend_provider(source).spend_rows_by_account(account_ids=[])
                self.assertEqual(rows, [])
                mock_objects.all.assert_not_called()

    def test_spend_rows_filters_by_account_and_date_range(self):
        with patch(_FB_QS) as mock_objects:
            qs = mock_objects.all.return_value
            qs.filter.return_value = qs
            qs.values.return_value.annotate.return_value = [{"spend": 1}]

            rows = ads_spend_provider(AdsSpendSource.FACEBOOK).spend_rows_by_account(
                account_ids=[7],
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 5),
            )

        self.assertEqual(rows, [{"spend": 1}])
        applied = [call.kwargs for call in qs.filter.call_args_list]
        self.assertIn({"date__gte": date(2026, 8, 1)}, applied)
        self.assertIn({"date__lte": date(2026, 8, 5)}, applied)
        self.assertIn({"ad_account_id__in": [7]}, applied)

    def test_total_spend_returns_zero_when_no_rows(self):
        """Aggregate rỗng → Decimal('0'), không phải None — caller nhân trực tiếp.

        Chuỗi query đi qua ``values().annotate()`` vì ``total_spend`` gộp bản sao
        của cùng một TKQC thật trước khi cộng. Đây là test WIRING (SimpleTestCase,
        không có DB, cố ý không import model của hai app quảng cáo); phép gộp thật
        được khẳng định ở ``test_ad_account_scope.py`` của từng app trên DB thật.
        """
        for source, path in (
            (AdsSpendSource.FACEBOOK, _FB_QS),
            (AdsSpendSource.GOOGLE, _GG_QS),
        ):
            with self.subTest(source=source), patch(path) as mock_objects:
                grouped = mock_objects.all.return_value.values.return_value.annotate
                grouped.return_value.aggregate.return_value = {"total": None}
                self.assertEqual(ads_spend_provider(source).total_spend(), Decimal("0"))

    def test_no_date_filter_applied_when_dates_are_none(self):
        with patch(_GG_QS) as mock_objects:
            qs = mock_objects.all.return_value
            qs.values.return_value.annotate.return_value.aggregate.return_value = {
                "total": Decimal("5")
            }
            total = ads_spend_provider(AdsSpendSource.GOOGLE).total_spend()
        self.assertEqual(total, Decimal("5"))
        qs.filter.assert_not_called()
