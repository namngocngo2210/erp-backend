"""Grain campaign của Google Ads: danh mục + chi phí theo ngày."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.google_ads.infrastructure import campaign_sync
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)


class CampaignModelTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.account = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC", customer_id="3000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )

    def test_campaign_id_unique_per_account_only(self):
        other = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC 2", customer_id="4000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        GoogleAdsCampaign.objects.create(ad_account=self.account, campaign_id="99", name="A")
        # Cùng campaign_id ở account khác là hợp lệ — id chỉ unique trong 1 customer.
        GoogleAdsCampaign.objects.create(ad_account=other, campaign_id="99", name="B")

        with transaction.atomic(), self.assertRaises(IntegrityError):
            GoogleAdsCampaign.objects.create(ad_account=self.account, campaign_id="99", name="C")

    def test_one_spend_row_per_campaign_and_date(self):
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.account, campaign_id="99", name="A",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("10.00"),
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            GoogleAdsCampaignDailySpend.objects.create(
                campaign=campaign, date=date(2026, 8, 1), spend=Decimal("20.00"),
            )


def _gaql_row(campaign_id, name, day, cost_micros, *, status="ENABLED", impressions=100, clicks=5):
    return SimpleNamespace(
        campaign=SimpleNamespace(id=campaign_id, name=name, status=status),
        segments=SimpleNamespace(date=day),
        metrics=SimpleNamespace(
            cost_micros=cost_micros, impressions=impressions, clicks=clicks,
        ),
    )


class CampaignSyncTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.account = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC", customer_id="3000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )

    def _sync(self, rows_by_customer, **kwargs):
        def fake_search(client, customer_id, query):
            value = rows_by_customer.get(customer_id, [])
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(campaign_sync, "_build_client", return_value=object()), \
             patch.object(campaign_sync, "_search", side_effect=fake_search) as mock_search:
            result = campaign_sync.sync_campaign_spend_for_config(self.config, **kwargs)
        return result, mock_search

    def test_one_query_fills_catalog_and_spend(self):
        rows = {
            "3000000000": [
                _gaql_row("99", "CD Tháng 8", "2026-08-01", 1_500_000),
                _gaql_row("99", "CD Tháng 8", "2026-08-02", 2_000_000),
                _gaql_row("77", "CD Khác", "2026-08-01", 500_000),
            ]
        }
        result, mock_search = self._sync(rows)

        # Đúng MỘT lời gọi API cho cả danh mục lẫn số liệu.
        self.assertEqual(mock_search.call_count, 1)
        self.assertEqual(result["rows_upserted"], 3)
        self.assertEqual(GoogleAdsCampaign.objects.count(), 2)
        self.assertEqual(GoogleAdsCampaignDailySpend.objects.count(), 3)

        campaign = GoogleAdsCampaign.objects.get(campaign_id="99")
        self.assertEqual(campaign.name, "CD Tháng 8")
        self.assertEqual(campaign.status, "ENABLED")
        spend = GoogleAdsCampaignDailySpend.objects.get(campaign=campaign, date=date(2026, 8, 1))
        # cost_micros / 1_000_000
        self.assertEqual(spend.spend, Decimal("1.50"))

    def test_reads_proto_enum_status(self):
        rows = {
            "3000000000": [
                _gaql_row("99", "CD", "2026-08-01", 1_000_000, status=SimpleNamespace(name="PAUSED")),
            ]
        }
        self._sync(rows)
        self.assertEqual(GoogleAdsCampaign.objects.get().status, "PAUSED")

    def test_rerun_updates_instead_of_duplicating(self):
        self._sync({"3000000000": [_gaql_row("99", "CD cũ", "2026-08-01", 1_000_000)]})
        self._sync({"3000000000": [_gaql_row("99", "CD đổi tên", "2026-08-01", 3_000_000)]})

        self.assertEqual(GoogleAdsCampaign.objects.count(), 1)
        self.assertEqual(GoogleAdsCampaign.objects.get().name, "CD đổi tên")
        self.assertEqual(GoogleAdsCampaignDailySpend.objects.count(), 1)
        self.assertEqual(GoogleAdsCampaignDailySpend.objects.get().spend, Decimal("3.00"))

    def test_backfill_window_for_account_without_data(self):
        _, mock_search = self._sync({"3000000000": []})
        query = mock_search.call_args[0][2]
        today = timezone.localdate()
        expected_since = (today - timedelta(days=campaign_sync.BACKFILL_DAYS)).isoformat()
        self.assertIn(expected_since, query)

    def test_recent_window_for_account_with_data(self):
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.account, campaign_id="99", name="A",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("1.00"),
        )
        _, mock_search = self._sync({"3000000000": []})
        query = mock_search.call_args[0][2]
        today = timezone.localdate()
        expected_since = (today - timedelta(days=campaign_sync.RECENT_DAYS)).isoformat()
        self.assertIn(expected_since, query)

    def test_failing_account_does_not_block_the_next_one(self):
        good = GoogleAdsAccount.objects.create(
            config=self.config, name="Tốt", customer_id="4000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        rows = {
            "3000000000": campaign_sync.GoogleAdsApiError("CUSTOMER_NOT_ENABLED"),
            "4000000000": [_gaql_row("55", "CD tốt", "2026-08-01", 1_000_000)],
        }
        result, _ = self._sync(rows)

        self.assertEqual(result["failed_accounts"], 1)
        self.assertEqual(result["rows_upserted"], 1)
        self.assertTrue(GoogleAdsCampaign.objects.filter(ad_account=good).exists())

    def test_removed_account_is_skipped(self):
        self.account.status = GoogleAdsAccount.STATUS_REMOVED
        self.account.save(update_fields=["status"])
        result, mock_search = self._sync({})
        self.assertEqual(result["accounts"], 0)
        self.assertEqual(mock_search.call_count, 0)

    def test_explicit_range_overrides_per_account_window(self):
        _, mock_search = self._sync(
            {"3000000000": []}, since="2026-01-01", until="2026-01-31",
        )
        query = mock_search.call_args[0][2]
        self.assertIn("2026-01-01", query)
        self.assertIn("2026-01-31", query)
