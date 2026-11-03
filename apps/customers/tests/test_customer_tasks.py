"""Cron precompute report khách hàng (rebuild_segment_snapshot).

Trọng tâm: chu kỳ có bước hỏng PHẢI báo ra ngoài. Sự cố 2026-07-22: 2/5 bước chết vì
statement timeout nhưng task vẫn ``succeeded``, không alert nào nổ — dashboard xu hướng
đứng số và drift loại khách tích tụ trong im lặng.
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from apps.customers import tasks as customer_tasks
from apps.customers.tasks import PrecomputeStepsFailed, rebuild_segment_snapshot


class RebuildSegmentSnapshotTests(TestCase):
    def tearDown(self):
        # Chỉ xoá đúng khoá của test này — KHÔNG cache.clear() (sẽ FLUSHDB Redis thật).
        cache.delete(customer_tasks._SEGMENT_LOCK_KEY)

    def test_returns_summary_when_all_steps_succeed(self):
        out = rebuild_segment_snapshot()

        self.assertIsInstance(out, dict)
        self.assertFalse(out["skipped"])
        self.assertEqual(out["failed"], [])
        self.assertIsInstance(out["rows"], int)

    def test_raises_when_a_step_fails(self):
        """Bước hỏng phải làm task fail — nếu không Celery ghi nhận thành công.

        Sau khi gỡ 3 bước report (cohort/trend/channel quality), bước duy nhất còn
        lại trong danh sách collect-then-raise là ``reconcile_customer_type``, và
        nó chỉ chạy ở lần full.
        """
        with mock.patch(
            "apps.customers.tasks.reconcile_customer_type",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(PrecomputeStepsFailed) as ctx:
                rebuild_segment_snapshot(full=True)

        self.assertIn("customer type reconcile", str(ctx.exception))

    def test_incremental_cycle_skips_customer_type_reconcile(self):
        """Nhịp 2h không chạy reconcile (audit 2026-08-19): UPDATE full-scan 4,2s
        chỉ thuộc về lần full nightly — drift mà nó vớt vốn hiếm."""
        with mock.patch("apps.customers.tasks.reconcile_customer_type") as reconcile:
            rebuild_segment_snapshot()

        reconcile.assert_not_called()

    def test_skips_when_lock_already_held(self):
        cache.add(customer_tasks._SEGMENT_LOCK_KEY, "other-owner", 60)

        out = rebuild_segment_snapshot()

        self.assertTrue(out["skipped"])
        self.assertEqual(out["failed"], [])


class SegmentSnapshotCadenceTests(TestCase):
    """Nhịp 2h phải là TĂNG DẦN, và phải còn một lần FULL mỗi ngày.

    Hai entry beat này là toàn bộ hợp đồng vận hành của việc cắt 23% thời gian DB
    (đo 2026-08-11). Mất entry full = drift tích tụ trong im lặng; entry 2h lỡ gọi
    full = mất trọn phần tiết kiệm. Cả hai đều không có lỗi nào nổ ra, nên khoá
    bằng test là cách duy nhất biết được.
    """

    def tearDown(self):
        cache.delete(customer_tasks._SEGMENT_LOCK_KEY)

    def test_two_hourly_run_is_incremental(self):
        with mock.patch(
            "apps.customers.segment_snapshot_service.SegmentSnapshotService.rebuild_incremental",
            return_value=(3, False),
        ) as incremental, mock.patch(
            "apps.customers.segment_snapshot_service.SegmentSnapshotService.rebuild"
        ) as full:
            out = rebuild_segment_snapshot()

        incremental.assert_called_once_with()
        full.assert_not_called()
        self.assertEqual(out["rows"], 3)

    def test_full_flag_rebuilds_everything(self):
        with mock.patch(
            "apps.customers.segment_snapshot_service.SegmentSnapshotService.rebuild",
            return_value=7,
        ) as full, mock.patch(
            "apps.customers.segment_snapshot_service.SegmentSnapshotService.rebuild_incremental"
        ) as incremental:
            out = rebuild_segment_snapshot(full=True)

        # `skip_unchanged=True` CHỈ ở đường full: lần full ghi đè cả bảng để đổi
        # vài trăm dòng. Đường tăng dần (test trên) phải gọi không tham số, vì
        # nó cần ghi để đẩy mốc nước `MAX(updated_at)` tiến lên.
        full.assert_called_once_with(skip_unchanged=True)
        incremental.assert_not_called()
        self.assertEqual(out["rows"], 7)

    def test_beat_keeps_both_cadences(self):
        from django.conf import settings

        entries = [
            e for e in settings.CELERY_BEAT_SCHEDULE.values()
            if e["task"] == "apps.customers.tasks.rebuild_segment_snapshot"
        ]
        self.assertEqual(len(entries), 2, "phải có đúng 2 entry: nhịp 2h + full hằng ngày")
        full_entries = [e for e in entries if e.get("kwargs", {}).get("full")]
        self.assertEqual(len(full_entries), 1, "thiếu (hoặc thừa) entry full hằng ngày")
        incremental = [e for e in entries if not e.get("kwargs", {}).get("full")]
        self.assertEqual(len(incremental), 1, "nhịp 2h phải chạy tăng dần, không truyền full")
