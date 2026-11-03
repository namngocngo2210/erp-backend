"""Cron cập nhật trạng thái đơn KiotViet: phạm vi quét (window + targets).

Sự cố 2026-09-16: cron 30' chạy đều, 0 lỗi, nhưng vận hành vẫn phải chạy tay
``import_kiotviet_invoices --update-status --from <tháng trước>`` vì:
- window chỉ từ ngày 1 THÁNG HIỆN TẠI (filter purchaseDate) → hoá đơn mua tháng trước
  đổi trạng thái sang tháng này (giao xong / hoàn / huỷ) không bao giờ được quét lại;
- default ``KIOTVIET_STATUS_SYNC_TARGETS`` thiếu kênh 5 (30965) và kho 241190 của kênh 4
  trong khi cron import create-only vẫn tạo đơn cho chúng → đơn kẹt "Chờ xử lý".
"""

from datetime import timedelta
from unittest import mock

from celery.exceptions import Retry
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.kiotviet import tasks as kv_tasks

_LOCK_KEY = "lock:update_invoice_status_targets"
_ONE_TARGET = [{"channel_id": 4, "branch_ids": ["231174"]}]


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@override_settings(KIOTVIET_STATUS_SYNC_TARGETS=_ONE_TARGET)
class UpdateInvoiceStatusWindowTests(SimpleTestCase):
    def setUp(self):
        cache.delete(_LOCK_KEY)

    def tearDown(self):
        cache.delete(_LOCK_KEY)

    @mock.patch.object(kv_tasks, "_update_status_for_target", return_value={})
    def test_regular_tick_scans_from_first_of_month(self, mock_update):
        result = kv_tasks.update_invoice_status_targets_task.apply().get()

        anchor = timezone.localtime() - timedelta(days=1)
        expected_from = _fmt(anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
        self.assertEqual(result["window"]["from"], expected_from)
        self.assertEqual(mock_update.call_args.kwargs["from_dt"], expected_from)

    @mock.patch.object(kv_tasks, "_update_status_for_target", return_value={})
    def test_deep_sweep_scans_lookback_days(self, mock_update):
        """Quét sâu lùi N ngày (00:00) → hiện tại: phủ trọn tháng trước dù là ngày nào."""
        result = kv_tasks.update_invoice_status_deep_task.apply(
            kwargs={"lookback_days": 62}
        ).get()

        now = timezone.localtime()
        expected_from = _fmt(
            (now - timedelta(days=62)).replace(hour=0, minute=0, second=0, microsecond=0)
        )
        self.assertEqual(result["window"]["from"], expected_from)
        self.assertEqual(mock_update.call_args.kwargs["from_dt"], expected_from)
        self.assertEqual(mock_update.call_args.kwargs["channel_id"], 4)
        self.assertEqual(mock_update.call_args.kwargs["branch_ids"], ["231174"])

    @mock.patch.object(kv_tasks, "_update_status_for_target", return_value={})
    def test_regular_tick_skips_when_lock_held(self, mock_update):
        self.assertTrue(cache.add(_LOCK_KEY, "other", 30))

        result = kv_tasks.update_invoice_status_targets_task.apply().get()

        self.assertEqual(result, {"skipped": "already_running"})
        mock_update.assert_not_called()

    @mock.patch.object(kv_tasks, "_update_status_for_target", return_value={})
    def test_deep_sweep_retries_instead_of_skipping_when_lock_held(self, mock_update):
        """Quét sâu 1 lần/đêm thua lock với tick 30' mà bỏ luôn là mất nguyên đêm."""
        self.assertTrue(cache.add(_LOCK_KEY, "other", 30))

        # Eager mode chạy retry NGAY (bỏ countdown) tới hết max_retries → mock để kiểm
        # tra task có gọi retry với countdown, thay vì chờ MaxRetriesExceededError.
        with mock.patch.object(
            kv_tasks.update_invoice_status_deep_task, "retry", side_effect=Retry("lock held")
        ) as mock_retry:
            res = kv_tasks.update_invoice_status_deep_task.apply(kwargs={"lookback_days": 62})

        self.assertEqual(res.status, "RETRY")
        mock_retry.assert_called_once()
        self.assertEqual(mock_retry.call_args.kwargs["countdown"], 300)
        mock_update.assert_not_called()

    @mock.patch.object(kv_tasks, "_update_status_for_target", return_value={})
    def test_deep_sweep_releases_lock(self, mock_update):
        kv_tasks.update_invoice_status_deep_task.apply(kwargs={"lookback_days": 62}).get()

        self.assertTrue(cache.add(_LOCK_KEY, "probe", 5))


class UpdateInvoiceStatusScheduleTests(SimpleTestCase):
    def test_deep_sweep_beat_entry_covers_previous_month(self):
        entry = settings.CELERY_BEAT_SCHEDULE["kv-update-invoice-status-deep"]

        self.assertEqual(entry["task"], "apps.kiotviet.tasks.update_invoice_status_deep")
        # 62 = tối đa (31 ngày tháng này + 31 ngày tháng trước): chạy ngày 31 vẫn tới ngày 1 tháng trước.
        self.assertGreaterEqual(entry["kwargs"]["lookback_days"], 62)

    def test_regular_tick_beat_entry_unchanged(self):
        entry = settings.CELERY_BEAT_SCHEDULE["kv-update-invoice-status"]

        self.assertEqual(entry["task"], "apps.kiotviet.tasks.update_invoice_status_targets")
        self.assertEqual(entry["schedule"], 30 * 60.0)


# Giá trị prod hiện hành của KIOTVIET_STATUS_SYNC_TARGETS — trước audit 2026-09-23 nằm
# hard-code làm default trong base.py, nay ops khai trong .env (xem
# docs/runbooks/2026-09-23-audit-ops.md mục Backend). Ghim ở đây để test bất biến
# "status targets phủ import targets" vẫn chạy được khi env test không set biến.
_PROD_STATUS_TARGETS_JSON = (
    '[{"channel_id": 4, "branch_ids": ["231174", "241190"]},'
    ' {"channel_id": 5, "branch_ids": ["30965"]},'
    ' {"channel_id": 6, "branch_ids": ["89084"]}]'
)


class StatusTargetsCoverImportTargetsTests(SimpleTestCase):
    @staticmethod
    def _status_pairs(targets):
        return {(int(t["channel_id"]), str(b)) for t in targets for b in t["branch_ids"]}

    def _assert_covers_import_targets(self, targets):
        """Cron import tạo đơn cho (kênh, kho) nào thì cron status phải quét (kênh, kho) đó,
        nếu không đơn sinh ra kẹt "Chờ xử lý" tới khi chạy tay --update-status."""
        import_pairs = set(
            kv_tasks._parse_import_create_only_targets(
                settings.KIOTVIET_IMPORT_CREATE_ONLY_TARGETS
            )
        )
        self.assertTrue(import_pairs, "cần ít nhất 1 target import để test có nghĩa")
        self.assertLessEqual(import_pairs, self._status_pairs(targets))

    def test_documented_prod_status_targets_cover_import_targets(self):
        import json

        targets = json.loads(_PROD_STATUS_TARGETS_JSON)
        self._assert_covers_import_targets(targets)
        # Sự cố 2026-09-16: thiếu kho thứ hai của kênh 4.
        self.assertIn((4, "241190"), self._status_pairs(targets))

    def test_configured_status_targets_cover_import_targets(self):
        if not settings.KIOTVIET_STATUS_SYNC_TARGETS:
            self.skipTest("KIOTVIET_STATUS_SYNC_TARGETS không set ở môi trường test (prod bắt buộc qua env)")
        self._assert_covers_import_targets(settings.KIOTVIET_STATUS_SYNC_TARGETS)
