"""Ghi theo lô thay vì từng dòng qua PgBouncer (audit 2026-09-23 T1).

- `_upsert_daily_rows`: 1 upsert cho cả cửa sổ ngày thay vì update_or_create/dòng
  (111k UPDATE/20h trên facebook_ad_account_daily_spends).
- `_upsert_catalog`: campaign/ad tra `__in` một lần, chỉ UPDATE khi field đổi, phần
  chỉ cần dấu last_seen_at gom một `update()` — số query hằng theo số ad.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.facebook_ads import services
from apps.facebook_ads.infrastructure import ad_catalog_sync
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookAdCampaign,
    FacebookApp,
)


def _ad(ad_id: str, campaign_id: str = "c1", name: str = "Ad") -> dict:
    return {
        "id": ad_id,
        "name": name,
        "campaign": {"id": campaign_id, "name": f"Camp {campaign_id}"},
        "creative": {"effective_object_story_id": "111_999"},
    }


class DailyRowsUpsertTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(name="App", app_id="1", app_secret="s", access_token="t")
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="9", fb_id="act_9",
            status=FacebookAdAccount.STATUS_ACTIVE,
        )

    def _rows(self, days: int) -> list[dict]:
        today = timezone.localdate()
        return [
            {"date_start": (today - timedelta(days=i)).isoformat(), "spend": f"{i}.50",
             "impressions": str(i), "clicks": "1", "reach": "2"}
            for i in range(days)
        ]

    def test_single_query_regardless_of_row_count(self):
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(services._upsert_daily_rows(self.account, self._rows(5)), 5)
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(services._upsert_daily_rows(self.account, self._rows(15)), 15)
        self.assertEqual(len(small.captured_queries), len(large.captured_queries))
        self.assertEqual(len(large.captured_queries), 1)
        self.assertEqual(FacebookAdAccountDailySpend.objects.filter(ad_account=self.account).count(), 15)

    def test_rerun_updates_in_place_and_dedupes_same_day(self):
        today = timezone.localdate().isoformat()
        services._upsert_daily_rows(self.account, [
            {"date_start": today, "spend": "1.00"},
            {"date_start": today, "spend": "7.00"},  # bản SAU thắng
        ])
        row = FacebookAdAccountDailySpend.objects.get(ad_account=self.account, date=today)
        self.assertEqual(row.spend, Decimal("7.00"))
        self.assertEqual(FacebookAdAccountDailySpend.objects.count(), 1)


class CatalogUpsertQueryCountTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(name="App", app_id="2", app_secret="s", access_token="t")
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="8", fb_id="act_8",
        )

    def _sync(self, ads: list[dict]) -> int:
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=ads), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page"}):
            with CaptureQueriesContext(connection) as ctx:
                ad_catalog_sync.sync_ad_catalog_for_app(self.app)
        return len(ctx.captured_queries)

    def test_query_count_constant_n5_vs_n15(self):
        small_ads = [_ad(f"s{i}", campaign_id=f"cs{i % 2}") for i in range(5)]
        self._sync(small_ads)
        small = self._sync(small_ads)  # lượt 2: mọi ad/campaign đã có → chỉ đóng dấu
        large_ads = [_ad(f"l{i}", campaign_id=f"cl{i % 3}") for i in range(15)]
        self._sync(large_ads)
        large = self._sync(large_ads)
        self.assertEqual(large, small, f"N+1 catalog: 5 ad = {small} query, 15 ad = {large}.")
        self.assertEqual(FacebookAd.objects.count(), 20)
        self.assertEqual(FacebookAdCampaign.objects.count(), 5)

    def test_unchanged_ads_only_get_last_seen_stamp(self):
        ads = [_ad("u1"), _ad("u2")]
        self._sync(ads)
        first_seen = FacebookAd.objects.get(fb_ad_id="u1").last_seen_at
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=ads), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            with CaptureQueriesContext(connection) as ctx:
                ad_catalog_sync.sync_ad_catalog_for_app(self.app)
        ad_updates = [q["sql"] for q in ctx.captured_queries if q["sql"].startswith("UPDATE") and "facebook_ads" in q["sql"]]
        # Một UPDATE ... WHERE id IN (...) cho cả lô ad không đổi — không phải 1/ad.
        self.assertEqual(len(ad_updates), 1, ad_updates)
        self.assertGreater(FacebookAd.objects.get(fb_ad_id="u1").last_seen_at, first_seen)

    def test_rename_and_campaign_change_still_applied(self):
        self._sync([_ad("r1", campaign_id="c1", name="Cũ")])
        self._sync([_ad("r1", campaign_id="c2", name="Mới")])
        ad = FacebookAd.objects.get(fb_ad_id="r1")
        self.assertEqual(ad.name, "Mới")
        self.assertEqual(ad.campaign.fb_campaign_id, "c2")
        self.assertEqual(FacebookAdCampaign.objects.get(fb_campaign_id="c1").name, "Camp c1")
