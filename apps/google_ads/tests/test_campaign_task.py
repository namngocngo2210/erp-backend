"""Task định kỳ và lệnh backfill cho chi phí grain chiến dịch."""

from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.google_ads import tasks
from apps.google_ads.management.commands.backfill_gads_campaign_spend import month_chunks
from apps.google_ads.models import GoogleAdsConfig
from apps.google_ads.services import GoogleAdsApiError


class SyncAllCampaignSpendTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )

    def test_aggregates_summary_across_configs(self):
        with patch.object(
            tasks, "sync_campaign_spend_for_config",
            return_value={"accounts": 3, "rows_upserted": 42, "failed_accounts": 1},
        ):
            summary = tasks._sync_all_campaign_spend_google()

        self.assertEqual(summary["configs"], 1)
        self.assertEqual(summary["rows_upserted"], 42)
        self.assertEqual(summary["failed_accounts"], 1)
        self.assertEqual(summary["errors"], [])

    def test_api_error_on_one_config_does_not_stop_the_rest(self):
        GoogleAdsConfig.objects.create(
            name="MCC 2", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="2000000000",
        )

        def fake_sync(config):
            if config.id == self.config.id:
                raise GoogleAdsApiError("token hỏng")
            return {"accounts": 1, "rows_upserted": 5, "failed_accounts": 0}

        with patch.object(tasks, "sync_campaign_spend_for_config", side_effect=fake_sync):
            summary = tasks._sync_all_campaign_spend_google()

        self.assertEqual(summary["configs"], 1)
        self.assertEqual(summary["rows_upserted"], 5)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["config_id"], self.config.id)

    def test_skips_when_another_run_holds_the_lock(self):
        with patch.object(tasks, "single_flight") as mock_lock:
            mock_lock.return_value.__enter__.return_value = False
            result = tasks.sync_all_campaign_spend_google()
        self.assertEqual(result, {"skipped": "already_running"})


class BackfillCommandTests(TestCase):
    def test_month_chunks_never_cross_a_month_boundary(self):
        chunks = month_chunks(date(2026, 1, 15), date(2026, 3, 3))
        self.assertEqual(
            chunks,
            [
                ("2026-01-15", "2026-01-31"),
                ("2026-02-01", "2026-02-28"),
                ("2026-03-01", "2026-03-03"),
            ],
        )

    def test_command_calls_sync_once_per_chunk(self):
        GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        target = (
            "apps.google_ads.management.commands."
            "backfill_gads_campaign_spend.sync_campaign_spend_for_config"
        )
        with patch(target, return_value={"rows_upserted": 2, "failed_accounts": 0}) as mock_sync:
            call_command(
                "backfill_gads_campaign_spend",
                "--since", "2026-01-15", "--until", "2026-03-03",
                stdout=StringIO(),
            )
        self.assertEqual(mock_sync.call_count, 3)

    def test_command_rejects_reversed_range(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command(
                "backfill_gads_campaign_spend",
                "--since", "2026-03-03", "--until", "2026-01-15",
                stdout=StringIO(),
            )
