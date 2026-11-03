"""Tests cho cron import create-only: danh sách kênh cấu hình qua
``settings.KIOTVIET_IMPORT_CREATE_ONLY_TARGETS`` (parser
``_parse_import_create_only_targets``) và fan-out mỗi kênh một sub-task."""

from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.kiotviet import tasks as kv_tasks
from apps.kiotviet.models import KiotVietChannel

# Giá trị default của KIOTVIET_IMPORT_CREATE_ONLY_TARGETS (giữ đồng bộ với
# backend/config/settings_parts/base.py) — dùng làm target cố định cho các test
# hành vi cũ (trước đây là hằng số IMPORT_CREATE_ONLY_TARGETS trong tasks.py).
DEFAULT_TARGETS: tuple[tuple[int, str], ...] = (
    (4, "231174"),
    (6, "89084"),
    (5, "30965"),
)


def _create_channel(channel_id: int, *, branch_id: str = "999999", **extra) -> KiotVietChannel:
    return KiotVietChannel.objects.create(
        id=channel_id,
        name=f"KV {channel_id}",
        branch_id=branch_id,
        branch_ids=[branch_id],
        client_id=f"client-{channel_id}",
        client_secret=f"secret-{channel_id}",
        retailer=f"retailer-{channel_id}",
        **extra,
    )


class ParseImportCreateOnlyTargetsTests(SimpleTestCase):
    def test_parses_default_setting_string(self):
        from django.conf import settings

        self.assertEqual(
            kv_tasks._parse_import_create_only_targets(
                settings.KIOTVIET_IMPORT_CREATE_ONLY_TARGETS
            ),
            DEFAULT_TARGETS,
        )

    def test_empty_string_yields_no_targets(self):
        self.assertEqual(kv_tasks._parse_import_create_only_targets(""), ())

    def test_whitespace_only_string_yields_no_targets(self):
        self.assertEqual(kv_tasks._parse_import_create_only_targets("   "), ())

    def test_parses_custom_string(self):
        self.assertEqual(
            kv_tasks._parse_import_create_only_targets("7:111,8:222"),
            ((7, "111"), (8, "222")),
        )

    def test_skips_malformed_part_missing_colon(self):
        # "abc" thiếu ":" — bị bỏ qua, các phần tử hợp lệ khác vẫn giữ.
        self.assertEqual(
            kv_tasks._parse_import_create_only_targets("4:231174,abc,6:89084"),
            ((4, "231174"), (6, "89084")),
        )

    def test_skips_malformed_part_non_integer_channel_id(self):
        # "abc:1" có ":" nhưng channel_id không phải số — bỏ qua, không raise.
        self.assertEqual(
            kv_tasks._parse_import_create_only_targets("abc:1,4:231174"),
            ((4, "231174"),),
        )

    def test_skips_blank_segments_between_commas(self):
        self.assertEqual(
            kv_tasks._parse_import_create_only_targets("4:231174,,6:89084"),
            ((4, "231174"), (6, "89084")),
        )


class IterImportChannelsTests(TestCase):
    def test_yields_hardcoded_targets_with_hardcoded_branch_ids(self):
        # branch_id trong DB CỐ TÌNH khác setting — kết quả phải lấy theo setting.
        for channel_id, _ in DEFAULT_TARGETS:
            _create_channel(channel_id, branch_id="000000")

        items = list(kv_tasks.iter_import_channels())

        self.assertEqual(
            [(i["channel_id"], i["branch_id"]) for i in items],
            list(DEFAULT_TARGETS),
        )
        self.assertTrue(all(not i["skip"] for i in items))

    def test_includes_old_channel_when_configured(self):
        # Kênh old=True (id=5) vẫn phải được import vì nằm trong danh sách cấu hình.
        for channel_id, _ in DEFAULT_TARGETS:
            _create_channel(channel_id, old=(channel_id == 5))

        items = {i["channel_id"]: i for i in kv_tasks.iter_import_channels()}

        self.assertFalse(items[5]["skip"])
        self.assertEqual(items[5]["branch_id"], "30965")

    def test_skips_missing_channel(self):
        _create_channel(4)
        _create_channel(5)
        # id=6 không tồn tại trong DB.

        items = {i["channel_id"]: i for i in kv_tasks.iter_import_channels()}

        self.assertTrue(items[6]["skip"])
        self.assertEqual(items[6]["reason"], "kênh không tồn tại hoặc đã xóa")
        self.assertFalse(items[4]["skip"])
        self.assertFalse(items[5]["skip"])

    def test_skips_soft_deleted_channel(self):
        _create_channel(4)
        _create_channel(5, deleted_at=timezone.now())
        _create_channel(6)

        items = {i["channel_id"]: i for i in kv_tasks.iter_import_channels()}

        self.assertTrue(items[5]["skip"])
        self.assertFalse(items[4]["skip"])
        self.assertFalse(items[6]["skip"])

    @override_settings(KIOTVIET_IMPORT_CREATE_ONLY_TARGETS="7:111222")
    def test_override_settings_changes_iter_targets(self):
        _create_channel(7, branch_id="111222")

        items = list(kv_tasks.iter_import_channels())

        self.assertEqual(
            [(i["channel_id"], i["branch_id"]) for i in items],
            [(7, "111222")],
        )

    @override_settings(KIOTVIET_IMPORT_CREATE_ONLY_TARGETS="")
    def test_empty_settings_string_yields_nothing(self):
        _create_channel(4)

        items = list(kv_tasks.iter_import_channels())

        self.assertEqual(items, [])


class ImportCreateOnlyFanOutTests(TestCase):
    """Task cha chỉ DISPATCH sub-task per-channel, không import trực tiếp."""

    @mock.patch.object(kv_tasks.import_create_only_one_channel_task, "apply_async")
    def test_dispatches_one_subtask_per_channel(self, mock_apply_async):
        for channel_id, _ in DEFAULT_TARGETS:
            _create_channel(channel_id)

        summary = kv_tasks.import_invoices_create_only_task.apply(
            kwargs={"from_dt": "2026-07-06 00:00:00", "to_dt": "2026-07-06 12:00:00"},
        ).get()

        expected = [cid for cid, _ in DEFAULT_TARGETS]
        self.assertEqual(summary["dispatched"], expected)
        self.assertEqual(summary["skipped"], [])
        self.assertEqual(mock_apply_async.call_count, len(expected))
        dispatched_kwargs = [c.kwargs["kwargs"] for c in mock_apply_async.call_args_list]
        self.assertEqual(
            [(k["channel_id"], k["branch_id"]) for k in dispatched_kwargs],
            list(DEFAULT_TARGETS),
        )
        for k in dispatched_kwargs:
            self.assertEqual(k["window_from"], "2026-07-06 00:00:00")
            self.assertEqual(k["window_to"], "2026-07-06 12:00:00")

    @mock.patch.object(kv_tasks.import_create_only_one_channel_task, "apply_async")
    def test_missing_channel_is_skipped_not_dispatched(self, mock_apply_async):
        _create_channel(4)
        _create_channel(5)
        # id=6 không có trong DB → skip, không dispatch.

        summary = kv_tasks.import_invoices_create_only_task.apply(
            kwargs={"from_dt": "2026-07-06 00:00:00", "to_dt": "2026-07-06 12:00:00"},
        ).get()

        self.assertEqual(summary["dispatched"], [4, 5])
        self.assertEqual(
            summary["skipped"],
            [{"channel_id": 6, "reason": "kênh không tồn tại hoặc đã xóa"}],
        )
        self.assertEqual(mock_apply_async.call_count, 2)

    @mock.patch.object(kv_tasks, "_import_invoices_for_target")
    def test_one_channel_subtask_respects_lock(self, mock_import):
        """Lock đang bị giữ → sub-task bỏ qua, KHÔNG gọi import (chống đơn trùng)."""
        from django.core.cache import cache

        _create_channel(4)
        lock_key = "kv:import_create:lock:4:231174"
        self.assertTrue(cache.add(lock_key, 1, 30))
        try:
            result = kv_tasks.import_create_only_one_channel_task.apply(
                kwargs={
                    "channel_id": 4,
                    "branch_id": "231174",
                    "window_from": "2026-07-06 00:00:00",
                    "window_to": "2026-07-06 12:00:00",
                },
            ).get()
        finally:
            cache.delete(lock_key)

        self.assertEqual(result["skipped_reason"], "đang chạy (lock)")
        mock_import.assert_not_called()

    @mock.patch.object(kv_tasks, "_import_invoices_for_target")
    def test_one_channel_subtask_imports_and_releases_lock(self, mock_import):
        from django.core.cache import cache

        _create_channel(4)
        mock_import.return_value = {"created": 2, "skipped": 1, "errors": 0}

        result = kv_tasks.import_create_only_one_channel_task.apply(
            kwargs={
                "channel_id": 4,
                "branch_id": "231174",
                "window_from": "2026-07-06 00:00:00",
                "window_to": "2026-07-06 12:00:00",
            },
        ).get()

        self.assertEqual(result, {"channel_id": 4, "created": 2, "skipped": 1, "errors": 0})
        mock_import.assert_called_once_with(
            channel_id=4,
            branch_ids=["231174"],
            create_only=True,
            from_dt="2026-07-06 00:00:00",
            to_dt="2026-07-06 12:00:00",
        )
        # Lock đã được nhả sau khi chạy xong → add lại được.
        self.assertTrue(cache.add("kv:import_create:lock:4:231174", 1, 5))
        cache.delete("kv:import_create:lock:4:231174")

    @mock.patch.object(kv_tasks, "_import_invoices_for_target")
    def test_lock_is_scoped_per_branch_not_per_channel(self, mock_import):
        """Cùng kênh khác branch KHÔNG chặn nhau: 1 kênh khai nhiều branch trong targets
        dispatch cùng lúc — lock chỉ theo channel làm branch sau bị bỏ đói vĩnh viễn."""
        from django.core.cache import cache

        _create_channel(4)
        mock_import.return_value = {"created": 0, "skipped": 0, "errors": 0}
        other_branch_lock = "kv:import_create:lock:4:111111"
        self.assertTrue(cache.add(other_branch_lock, 1, 30))
        try:
            result = kv_tasks.import_create_only_one_channel_task.apply(
                kwargs={
                    "channel_id": 4,
                    "branch_id": "231174",
                    "window_from": "2026-07-06 00:00:00",
                    "window_to": "2026-07-06 12:00:00",
                },
            ).get()
        finally:
            cache.delete(other_branch_lock)

        self.assertNotIn("skipped_reason", result)
        mock_import.assert_called_once()


FIXED_NOW = timezone.datetime(2026, 7, 18, 14, 30, 5)


class ImportWindowTests(SimpleTestCase):
    """Window cron create-only: lùi N ngày vì filter API theo purchaseDate (ngày mua) —
    hóa đơn backdate nằm ngoài window "hôm nay" vĩnh viễn nếu không lùi."""

    def test_zero_lookback_is_today_midnight_to_now(self):
        with mock.patch.object(kv_tasks.timezone, "localtime", return_value=FIXED_NOW):
            self.assertEqual(
                kv_tasks._import_window(0),
                ("2026-07-18 00:00:00", "2026-07-18 14:30:05"),
            )

    def test_lookback_days_shifts_window_start_only(self):
        with mock.patch.object(kv_tasks.timezone, "localtime", return_value=FIXED_NOW):
            self.assertEqual(
                kv_tasks._import_window(1),
                ("2026-07-17 00:00:00", "2026-07-18 14:30:05"),
            )
            self.assertEqual(
                kv_tasks._import_window(31),
                ("2026-06-17 00:00:00", "2026-07-18 14:30:05"),
            )


class ImportCreateOnlyWindowWiringTests(TestCase):
    """Fan-out task chọn window: settings lookback mặc định, kwarg lookback_days
    (beat entry quét sâu) hoặc from_dt/to_dt tường minh."""

    def setUp(self):
        for channel_id, _ in DEFAULT_TARGETS:
            _create_channel(channel_id)

    @override_settings(KIOTVIET_IMPORT_CREATE_ONLY_LOOKBACK_DAYS=2)
    @mock.patch.object(kv_tasks.import_create_only_one_channel_task, "apply_async")
    def test_default_window_uses_settings_lookback(self, mock_apply_async):
        with mock.patch.object(kv_tasks.timezone, "localtime", return_value=FIXED_NOW):
            summary = kv_tasks.import_invoices_create_only_task.apply().get()

        self.assertEqual(summary["window"]["from"], "2026-07-16 00:00:00")
        self.assertEqual(summary["window"]["to"], "2026-07-18 14:30:05")
        for call in mock_apply_async.call_args_list:
            self.assertEqual(call.kwargs["kwargs"]["window_from"], "2026-07-16 00:00:00")

    @mock.patch.object(kv_tasks.import_create_only_one_channel_task, "apply_async")
    def test_lookback_days_kwarg_overrides_settings(self, mock_apply_async):
        # Đường beat entry kv-import-create-only-deep: lookback_days=31.
        with mock.patch.object(kv_tasks.timezone, "localtime", return_value=FIXED_NOW):
            summary = kv_tasks.import_invoices_create_only_task.apply(
                kwargs={"lookback_days": 31},
            ).get()

        self.assertEqual(summary["window"]["from"], "2026-06-17 00:00:00")
        self.assertEqual(mock_apply_async.call_count, len(DEFAULT_TARGETS))

    @mock.patch.object(kv_tasks.import_create_only_one_channel_task, "apply_async")
    def test_explicit_from_to_wins_over_lookback(self, mock_apply_async):
        summary = kv_tasks.import_invoices_create_only_task.apply(
            kwargs={
                "from_dt": "2026-07-01 00:00:00",
                "to_dt": "2026-07-02 00:00:00",
                "lookback_days": 31,
            },
        ).get()

        self.assertEqual(summary["window"]["from"], "2026-07-01 00:00:00")
        self.assertEqual(summary["window"]["to"], "2026-07-02 00:00:00")


class MonthWindowBoundaryTests(SimpleTestCase):
    """Biên tháng của `_current_month_window` (audit 2026-07-18).

    Reconcile đổi lịch sang 00:00 — neo "tháng hiện tại" theo `now` thì run 00:00
    ngày 1 quét tháng MỚI rỗng, cả ngày cuối tháng trước vĩnh viễn không được đối
    soát. Neo theo now−1d: run ngày 1 phủ trọn tháng cũ, ngày thường không đổi.
    """

    def test_first_of_month_midnight_covers_previous_month(self):
        first_midnight = timezone.datetime(2026, 8, 1, 0, 0, 3)
        with mock.patch.object(kv_tasks.timezone, "localtime", return_value=first_midnight):
            window_from, window_to = kv_tasks._current_month_window()
        self.assertEqual(window_from, "2026-07-01 00:00:00")
        self.assertEqual(window_to, "2026-08-01 00:00:03")

    def test_mid_month_behaviour_unchanged(self):
        with mock.patch.object(kv_tasks.timezone, "localtime", return_value=FIXED_NOW):
            window_from, window_to = kv_tasks._current_month_window()
        self.assertEqual(window_from, "2026-07-01 00:00:00")
        self.assertEqual(window_to, "2026-07-18 14:30:05")


class CreateOnlySoftDeletedSkipTests(TestCase):
    """create_only phải coi đơn ĐÃ XOÁ (soft-delete) là 'đã tồn tại' — không thì
    lookback/deep-sweep resurrect đơn user vừa xoá mỗi đêm (audit 2026-07-18)."""

    def test_soft_deleted_order_not_resurrected(self):
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.kiotviet.infrastructure.services.import_invoices_service import (
            ImportInvoicesService,
        )
        from apps.orders.models import Order

        kv_channel = KiotVietChannel.objects.create(
            name="KV SD", branch_id="B1", retailer="r-sd",
            client_id="c-sd", client_secret="s-sd",
        )
        channel = Channel.objects.create(code="ch-sd", name="SD")
        customer = Customer.objects.create(
            full_name="KH SD", phone_raw="0905555000", phone_normalized="0905555000"
        )
        order = Order.objects.create(
            channel=channel, kiotviet_channel=kv_channel, customer=customer,
            name="KH SD", phone="0905555000",
            invoice_code="HD-SD-1", invoice_id=9911, total_amount="1000",
        )
        Order.objects.filter(pk=order.pk).update(deleted_at=timezone.now())

        result = ImportInvoicesService.upsert_invoice(
            invoice={"id": 9911, "code": "HD-SD-1", "status": 1, "total": 1000},
            kv_channel=kv_channel,
            dry_run=False,
            create_only=True,
        )
        self.assertEqual(result, "skipped")
        self.assertEqual(
            Order.objects.filter(invoice_code="HD-SD-1", deleted_at__isnull=True).count(),
            0,
            "đơn đã xoá không được resurrect",
        )
