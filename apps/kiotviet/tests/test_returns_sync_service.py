from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.returns_sync_service import (
    sync_returns_all_channels,
    sync_returns_for_channel,
)
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderRecall


def _resp(status=200, payload=None):
    response = MagicMock()
    response.status_code = status
    response.content = b"{}"
    response.text = ""
    response.json.return_value = payload or {}
    return response


def _ret(code, invoice_id, return_date="2026-07-01T10:00:00"):
    return {"id": 1, "code": code, "invoiceId": invoice_id, "returnDate": return_date}


def _empty_channel_summary():
    return {
        "read": 0, "matched": 0, "updated": 0, "skipped_no_order": 0,
        "skipped_no_code": 0, "skipped_no_new_codes": 0,
        "skipped_out_of_window": 0, "pages": 0,
    }


class SyncReturnsForChannelTests(TestCase):
    def setUp(self):
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV Returns",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
        )
        self.channel = Channel.objects.create(
            code="sales-returns",
            name="Sales Returns",
            owner="kiotviet",
        )
        self.customer = Customer.objects.create(
            full_name="Returns Customer",
            phone_raw="0909000222",
            phone_normalized="0909000222",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            kiotviet_channel=self.kv_channel,
            invoice_id=555,
            invoice_code="HD555",
        )

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_maps_return_to_order_by_invoice_id(self, mock_req):
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)], "total": 1})]

        summary = sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["matched"], 1)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_maps_whole_batch_with_one_query(self, mock_req):
        """Gom hết phiếu rồi map MỘT LƯỢT: số query tra đơn không tăng theo số phiếu."""
        orders = [self.order]
        for invoice_id in range(556, 566):
            orders.append(
                Order.objects.create(
                    channel=self.channel,
                    customer=self.customer,
                    kiotviet_channel=self.kv_channel,
                    invoice_id=invoice_id,
                    invoice_code=f"HD{invoice_id}",
                )
            )
        returns = [_ret(f"TH{o.invoice_id}", o.invoice_id) for o in orders]
        mock_req.side_effect = [_resp(payload={"data": returns})]

        # Chỉ ĐÚNG 1 query đọc bảng orders cho cả lô. N+1 quay lại → con số này = số phiếu.
        with CaptureQueriesContext(connection) as ctx:
            summary = sync_returns_for_channel(self.kv_channel)

        order_queries = [q for q in ctx.captured_queries if 'FROM "orders"' in q["sql"]]
        self.assertEqual(len(order_queries), 1, order_queries)

        self.assertEqual(summary["matched"], len(orders))
        self.assertEqual(summary["updated"], len(orders))

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_multiple_returns_same_invoice_sorted_and_deduped(self, mock_req):
        mock_req.side_effect = [
            _resp(payload={"data": [
                _ret("TH002", 555, "2026-07-02T10:00:00"),
                _ret("TH001", 555, "2026-07-01T10:00:00"),
                _ret("TH001", 555, "2026-07-01T10:00:00"),
            ]})
        ]

        sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001", "TH002"])

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_union_appends_new_kiotviet_code_and_keeps_manual_code(self, mock_req):
        OrderRecall.objects.create(order=self.order, return_slip_code=["MANUAL-1"])
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        summary = sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        # Human code preserved, still first; new KiotViet code appended, order preserved.
        self.assertEqual(recall.return_slip_code, ["MANUAL-1", "TH001"])
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["skipped_no_new_codes"], 0)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_kiotviet_code_already_present_is_skipped_and_counted(self, mock_req):
        OrderRecall.objects.create(order=self.order, return_slip_code=["TH001"])
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        summary = sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])
        self.assertEqual(summary["skipped_no_new_codes"], 1)
        self.assertEqual(summary["updated"], 0)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_existing_recall_row_with_empty_array_gets_filled(self, mock_req):
        # Recall row đã tồn tại (VD tạo trống trước đó) nhưng return_slip_code vẫn
        # rỗng — union phải vẫn điền vào, không coi dòng là "đã có sẵn giá trị".
        # Mã/ngày thu hồi (recall_code) đã tách sang OrderRecallEntry (Task 1), nên
        # OrderRecall giờ chỉ còn return_slip_code — bỏ field recall_code khỏi setup.
        OrderRecall.objects.create(order=self.order, return_slip_code=[])
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        summary = sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])
        self.assertEqual(summary["updated"], 1)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_unmatched_invoice_does_not_call_extra_api(self, mock_req):
        """Map trượt = bỏ qua, KHÔNG gọi /invoices/{id}. Fallback per-id từng làm KiotViet
        trả 420 (rate-limit) hàng nghìn lần mà không khớp thêm được đơn nào."""
        self.order.invoice_id = None
        self.order.save(update_fields=["invoice_id"])
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        summary = sync_returns_for_channel(self.kv_channel)

        self.assertEqual(summary["skipped_no_order"], 1)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(mock_req.call_count, 1)  # chỉ đúng 1 call /returns
        self.assertFalse(OrderRecall.objects.filter(order=self.order).exists())

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_paginates_until_short_page(self, mock_req):
        page1 = [_ret(f"TH{i:03d}", 555) for i in range(100)]
        mock_req.side_effect = [
            _resp(payload={"data": page1}),
            _resp(payload={"data": [_ret("TH999", 555)]}),
        ]

        summary = sync_returns_for_channel(self.kv_channel)

        self.assertEqual(summary["pages"], 2)
        self.assertEqual(summary["read"], 101)
        self.assertEqual(mock_req.call_args_list[0].kwargs["params"], {"pageSize": 100, "currentItem": 0})
        self.assertEqual(mock_req.call_args_list[1].kwargs["params"], {"pageSize": 100, "currentItem": 100})

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_last_modified_from_reaches_request_params(self, mock_req):
        # Regression: deleting the `params["lastModifiedFrom"] = ...` line in iter_returns
        # would pass the whole suite while silently turning the 15-minute cron into a
        # full-history scraper every run.
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        sync_returns_for_channel(self.kv_channel, last_modified_from="2026-07-11 08:00:00")

        self.assertEqual(
            mock_req.call_args_list[0].kwargs["params"]["lastModifiedFrom"],
            "2026-07-11 08:00:00",
        )

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_page_cap_raises_runtime_error_when_current_item_ignored(self, mock_req):
        from apps.kiotviet.infrastructure.services import returns_sync_service

        # KiotViet ignoring currentItem: every page returns a full page, so pagination
        # would never see a short page and would loop forever without the hard cap.
        full_page = [_ret(f"TH{i:03d}", 555) for i in range(100)]
        mock_req.side_effect = lambda *a, **k: _resp(payload={"data": full_page})

        with patch.object(returns_sync_service, "MAX_PAGES", 3):
            with self.assertRaises(RuntimeError):
                sync_returns_for_channel(self.kv_channel)

        # Dừng đúng ở trần: nếu trần bị gỡ, test treo thay vì đỏ — chốt bằng call_count.
        self.assertEqual(mock_req.call_count, 3)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_out_of_window_return_is_skipped_and_counted(self, mock_req):
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555, "2026-07-01T10:00:00")]})]

        summary = sync_returns_for_channel(self.kv_channel, date_from="2026-07-05", date_to="2026-07-10")

        self.assertEqual(summary["read"], 1)
        self.assertEqual(summary["skipped_out_of_window"], 1)
        self.assertEqual(summary["matched"], 0)
        self.assertEqual(summary["updated"], 0)
        self.assertFalse(OrderRecall.objects.filter(order=self.order).exists())

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_in_window_return_is_processed(self, mock_req):
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555, "2026-07-05T10:00:00")]})]

        summary = sync_returns_for_channel(self.kv_channel, date_from="2026-07-01", date_to="2026-07-10")

        self.assertEqual(summary["skipped_out_of_window"], 0)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["updated"], 1)
        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_unusable_return_date_with_window_set_is_skipped(self, mock_req):
        # Current behavior: returnDate rỗng/không parse được, hoặc hợp lệ về dạng nhưng
        # sai lịch (30/02 → parse_datetime raise ValueError) đều fail-closed ở
        # _in_date_window (coi như ngoài cửa sổ) khi có cấu hình cửa sổ.
        for return_date in ("", "2026-02-30T10:00:00"):
            with self.subTest(return_date=repr(return_date)):
                mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555, return_date)]})]

                summary = sync_returns_for_channel(
                    self.kv_channel, date_from="2026-07-01", date_to="2026-07-10"
                )

                self.assertEqual(summary["skipped_out_of_window"], 1)
                self.assertEqual(summary["matched"], 0)
                self.assertFalse(OrderRecall.objects.filter(order=self.order).exists())

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_invalid_calendar_date_without_window_is_written(self, mock_req):
        # parse_datetime raises ValueError (not None) for a well-formed but
        # calendrically invalid date like Feb 30 — must not crash the sync.
        mock_req.side_effect = [
            _resp(payload={"data": [_ret("TH001", 555, "2026-02-30T10:00:00")]})
        ]

        summary = sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["updated"], 1)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_bogus_invoice_id_is_skipped_and_counted_without_aborting_channel(self, mock_req):
        # invoiceId rác (non-numeric) trên 1 item không được làm crash cả trang — các
        # item hợp lệ khác (kể cả sau item rác) vẫn phải được xử lý.
        mock_req.side_effect = [
            _resp(payload={"data": [
                _ret("TH-BAD", "not-a-number"),
                _ret("TH001", 555),
            ]})
        ]

        summary = sync_returns_for_channel(self.kv_channel)

        self.assertEqual(summary["read"], 2)
        self.assertEqual(summary["skipped_invalid_invoice_id"], 1)
        self.assertEqual(summary["matched"], 1)
        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_unknown_invoice_is_skipped(self, mock_req):
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 999999)]})]

        summary = sync_returns_for_channel(self.kv_channel)

        self.assertEqual(summary["skipped_no_order"], 1)
        self.assertFalse(OrderRecall.objects.filter(order=self.order).exists())

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_dry_run_does_not_write(self, mock_req):
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        summary = sync_returns_for_channel(self.kv_channel, dry_run=True)

        self.assertEqual(summary["updated"], 1)
        self.assertFalse(OrderRecall.objects.filter(order=self.order).exists())

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_http_error_raises(self, mock_req):
        mock_req.side_effect = [_resp(status=500)]

        with self.assertRaises(RuntimeError):
            sync_returns_for_channel(self.kv_channel)


class SyncReturnsAllChannelsTests(TestCase):
    def setUp(self):
        self.kv_ok = KiotVietChannel.objects.create(
            name="KV OK",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id-ok",
            client_secret="kv-client-secret-ok",
            retailer="kv-retailer-ok",
        )
        self.kv_bad = KiotVietChannel.objects.create(
            name="KV Bad",
            branch_id="231175",
            branch_ids=["231175"],
            client_id="kv-client-id-bad",
            client_secret="kv-client-secret-bad",
            retailer="kv-retailer-bad",
        )

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service.sync_returns_for_channel")
    def test_old_mirror_channel_is_skipped(self, mock_sync):
        """Kênh cũ (old=True) không được sync: hoá đơn của nó nằm ở Order.old_id chứ không
        phải Order.invoice_id, map theo invoice_id sẽ trượt 100%."""
        old_channel = KiotVietChannel.objects.create(
            name="KV Old",
            branch_id="231176",
            branch_ids=["231176"],
            client_id="kv-client-id-old",
            client_secret="kv-client-secret-old",
            retailer="kv-retailer-old",
            old=True,
        )
        mock_sync.return_value = _empty_channel_summary()

        summary = sync_returns_all_channels()

        synced_ids = {call.args[0].id for call in mock_sync.call_args_list}
        self.assertNotIn(old_channel.id, synced_ids)
        self.assertEqual(synced_ids, {self.kv_ok.id, self.kv_bad.id})
        self.assertEqual(summary["channels"], 2)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service.sync_returns_for_channel")
    def test_one_channel_error_does_not_block_others(self, mock_sync):
        def side_effect(channel, **kwargs):
            if channel.id == self.kv_bad.id:
                raise RuntimeError("boom")
            return {"read": 1, "matched": 1, "updated": 1, "skipped_no_order": 0,
                    "skipped_no_code": 0, "skipped_no_new_codes": 0,
                    "skipped_out_of_window": 0, "pages": 1}

        mock_sync.side_effect = side_effect

        summary = sync_returns_all_channels()

        self.assertEqual(summary["updated"], 1)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("boom", summary["errors"][0]["error"])

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service.sync_returns_for_channel")
    def test_soft_time_limit_exceeded_propagates_instead_of_being_collected(self, mock_sync):
        # A Celery soft-timeout means the task is about to be hard-killed — it must abort
        # the whole run, not get swallowed into summary["errors"] while looping to the
        # next channel (which would just burn the remaining time until SIGKILL).
        from celery.exceptions import SoftTimeLimitExceeded

        mock_sync.side_effect = SoftTimeLimitExceeded("too slow")

        with self.assertRaises(SoftTimeLimitExceeded):
            sync_returns_all_channels()
