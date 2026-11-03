"""Hai entrypoint của sync trả hàng KiotViet: management command và Celery task.

Cả hai đều gọi vào cùng service sync_returns_* — logic service test ở
test_returns_sync_service.py; ở đây chỉ test wiring của từng entrypoint.

Lưu ý: setUp/tearDown của task dùng cache.delete(_SYNC_RETURNS_LOCK_KEY) có chủ đích
(xoá đúng 1 key lock) — KHÔNG thay bằng cache.clear() (FLUSHDB đè Redis dùng chung).
"""
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.kiotviet.models import KiotVietChannel
from apps.kiotviet.tasks import _SYNC_RETURNS_LOCK_KEY, sync_kiotviet_returns_task


class SyncKiotVietReturnsCommandTests(TestCase):
    def setUp(self):
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV Returns",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
        )

    @patch("apps.kiotviet.management.commands.sync_kiotviet_returns.sync_returns_all_channels")
    def test_defaults_to_all_channels(self, mock_all):
        mock_all.return_value = {"read": 0, "updated": 0, "errors": []}
        out = StringIO()

        call_command("sync_kiotviet_returns", stdout=out)

        mock_all.assert_called_once()
        self.assertEqual(mock_all.call_args.kwargs["dry_run"], False)
        self.assertIn('"updated": 0', out.getvalue())

    @patch("apps.kiotviet.management.commands.sync_kiotviet_returns.sync_returns_for_channel")
    def test_single_channel_with_date_window_and_dry_run(self, mock_one):
        mock_one.return_value = {"read": 0, "updated": 0}
        out = StringIO()

        call_command(
            "sync_kiotviet_returns",
            "--channel-id", str(self.kv_channel.id),
            "--from", "2026-07-01",
            "--to", "2026-07-05",
            "--dry-run",
            stdout=out,
        )

        args = mock_one.call_args.args
        kwargs = mock_one.call_args.kwargs
        self.assertEqual(args[0], self.kv_channel)
        self.assertEqual(kwargs["date_from"], "2026-07-01")
        self.assertEqual(kwargs["date_to"], "2026-07-05")
        self.assertTrue(kwargs["dry_run"])

    def test_unknown_channel_raises(self):
        with self.assertRaises(CommandError):
            call_command("sync_kiotviet_returns", "--channel-id", "999999")

    def test_soft_deleted_channel_raises(self):
        self.kv_channel.deleted_at = timezone.now()
        self.kv_channel.save(update_fields=["deleted_at"])

        with self.assertRaises(CommandError):
            call_command("sync_kiotviet_returns", "--channel-id", str(self.kv_channel.id))


class SyncKiotVietReturnsTaskTests(SimpleTestCase):
    def setUp(self):
        cache.delete(_SYNC_RETURNS_LOCK_KEY)

    def tearDown(self):
        cache.delete(_SYNC_RETURNS_LOCK_KEY)

    @patch("apps.kiotviet.tasks.sync_returns_all_channels")
    def test_passes_lookback_window(self, mock_sync):
        mock_sync.return_value = {"updated": 2, "errors": []}
        lookback_hours = 5  # non-default: catches "ignores lookback_hours" bugs

        before = timezone.localtime().replace(tzinfo=None)
        result = sync_kiotviet_returns_task(lookback_hours=lookback_hours)
        after = timezone.localtime().replace(tzinfo=None)

        self.assertEqual(result["updated"], 2)
        last_modified_from = mock_sync.call_args.kwargs["last_modified_from"]
        self.assertRegex(last_modified_from, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

        parsed = datetime.strptime(last_modified_from, "%Y-%m-%d %H:%M:%S")
        tolerance = timedelta(minutes=2)
        expected_min = before - timedelta(hours=lookback_hours) - tolerance
        expected_max = after - timedelta(hours=lookback_hours) + tolerance
        self.assertTrue(
            expected_min <= parsed <= expected_max,
            f"last_modified_from={parsed} not within tolerance of "
            f"now-{lookback_hours}h (expected {expected_min}..{expected_max}); "
            "check the window uses '-' (past) and honors lookback_hours",
        )

    @patch("apps.kiotviet.tasks.sync_returns_all_channels")
    def test_skips_when_lock_held(self, mock_sync):
        cache.add(_SYNC_RETURNS_LOCK_KEY, 1, 60)

        result = sync_kiotviet_returns_task()

        self.assertEqual(result, {"skipped": "lock_held"})
        mock_sync.assert_not_called()

    @patch("apps.kiotviet.tasks.sync_returns_all_channels")
    def test_errors_in_summary_are_logged_not_raised(self, mock_sync):
        mock_sync.return_value = {
            "updated": 0,
            "errors": [{"channel_id": 1, "error": "boom"}],
        }

        with self.assertLogs("apps.kiotviet.tasks", level="WARNING") as logs:
            result = sync_kiotviet_returns_task()

        self.assertEqual(result["errors"], [{"channel_id": 1, "error": "boom"}])
        self.assertTrue(any("errors" in message for message in logs.output))
