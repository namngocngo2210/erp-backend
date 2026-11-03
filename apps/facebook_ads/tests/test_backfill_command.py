"""Backfill chia theo tháng — mỗi lượt gọi phải nằm trong giới hạn payload Batch API."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.facebook_ads.management.commands import backfill_fb_ad_spend
from apps.facebook_ads.models import FacebookAdAccount, FacebookApp


class BackfillCommandTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )

    def test_splits_range_into_monthly_chunks(self):
        calls = []

        def _capture(app, *, since, until):
            calls.append((since, until))
            return {"rows_upserted": 0, "lazy_fetched_ads": 0, "failed_accounts": 0}

        with patch.object(backfill_fb_ad_spend, "sync_ad_spend_for_app", side_effect=_capture):
            call_command(
                "backfill_fb_ad_spend",
                "--since", "2026-01-01", "--until", "2026-03-15",
                stdout=StringIO(),
            )

        self.assertEqual(
            calls,
            [
                ("2026-01-01", "2026-01-31"),
                ("2026-02-01", "2026-02-28"),
                ("2026-03-01", "2026-03-15"),
            ],
        )

    def test_skips_app_with_invalid_token(self):
        self.app.token_status = FacebookApp.TOKEN_INVALID
        self.app.save(update_fields=["token_status"])

        with patch.object(backfill_fb_ad_spend, "sync_ad_spend_for_app") as sync:
            call_command(
                "backfill_fb_ad_spend",
                "--since", "2026-01-01", "--until", "2026-01-31",
                stdout=StringIO(),
            )

        sync.assert_not_called()

    def test_rejects_reversed_range(self):
        with self.assertRaises(Exception):
            call_command(
                "backfill_fb_ad_spend",
                "--since", "2026-03-01", "--until", "2026-01-01",
                stdout=StringIO(),
            )
