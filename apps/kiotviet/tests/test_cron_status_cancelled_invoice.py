"""Cron 30' (`update_invoice_status_targets`) KHÔNG được lật đơn HUỶ thành "đã giao".

Hoá đơn huỷ trên KiotViet (bán quầy POS / KV bỏ invoiceDelivery khi huỷ) không có
`invoiceDelivery.status`. Handler cũ chỉ đọc delivery rồi mặc định 3 → đơn CANCELLED bị
kéo về IN_DELIVERY. Từ khi cron phát `OrderStatusChanged`, cú lật đó còn kéo theo
`reissue_after_cancel_release` → TRỪ TỒN của đơn đã huỷ dù hàng vẫn nằm trong kho. Cron
quét cả tháng nên lần chạy đầu sau deploy sai hàng loạt (audit 2026-07-14 vòng 3).
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService
from apps.kiotviet.models import KiotVietChannel
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import Order, OrderStatus


class _FakeResponse:
    status_code = 200
    content = b"{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class CronCancelledInvoiceStatusTests(TestCase):
    def setUp(self):
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV Cron",
            branch_id="B1",
            retailer="RetailerCron",
            client_id="cli-cron",
            client_secret="secret-cron",
        )
        self.channel = Channel.objects.create(code="ch-cron", name="Cron Channel")
        self.customer = Customer.objects.create(
            full_name="KH Cron",
            phone_raw="0911222333",
            phone_normalized="0911222333",
            code="CUST-CRON",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            kiotviet_channel=self.kv_channel,
            customer=self.customer,
            name="KH Cron",
            phone="0911222333",
            invoice_code="HD-CRON-1",
            invoice_id=7001,
            total_amount="100000",
            status=OrderStatus.CANCELLED,
            invoice_status=2,
        )

    def _run_cron(self, invoice: dict):
        service = ImportInvoicesService()
        with patch(
            "apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request",
            return_value=_FakeResponse(invoice),
        ):
            service.update_status_phone(
                kv_channel=self.kv_channel,
                branch_ids=["B1"],
                invoice_codes=["HD-CRON-1"],
                dry_run=False,
                do_status=True,
                do_phone=False,
            )
        self.order.refresh_from_db()

    def test_cancelled_invoice_without_delivery_stays_cancelled(self):
        self._run_cron({"code": "HD-CRON-1", "id": 7001, "status": 2})

        self.assertEqual(int(self.order.status), int(OrderStatus.CANCELLED))
        self.assertEqual(self.order.invoice_status, 2)

    def test_completed_pos_invoice_without_delivery_still_maps_to_delivered(self):
        """Không được sửa quá tay: hoá đơn HOÀN THÀNH bán quầy vẫn phải về 'đã giao'."""
        self.order.status = OrderStatus.IN_DELIVERY
        self.order.invoice_status = 1
        self.order.save(update_fields=["status", "invoice_status"])

        self._run_cron({"code": "HD-CRON-1", "id": 7001, "status": 1})

        self.assertEqual(int(self.order.status), 3)

    def test_cancelled_invoice_does_not_publish_revive_event(self):
        """Không phát OrderStatusChanged giả → kho không trừ lại tồn của đơn đã huỷ."""
        with patch(
            "apps.kiotviet.infrastructure.services._import_service_handlers_mixin"
            ".event_bus.publish_on_commit"
        ) as publish:
            self._run_cron({"code": "HD-CRON-1", "id": 7001, "status": 2})

        publish.assert_not_called()


class CronReviveVerifiesFreshInvoiceTests(TestCase):
    """Cron đọc DANH SÁCH hoá đơn theo trang rồi mới ghi — snapshot có thể cũ vài phút.

    Sự cố 14/09/2026 (đơn 563397): cron đọc trang 1 lúc 16:22:11.06, Sale huỷ hoá đơn
    trên KiotViet lúc 16:22:11.58, cron ghi lúc 16:23:18 → đơn ĐÃ HUỶ bị lật về "Đang
    lấy hàng" kèm sự kiện "hồi sinh" cho kho. Trước khi đưa một đơn đang huỷ về trạng
    thái sống, cron phải hỏi lại KiotViet theo mã hoá đơn; KiotViet nói huỷ thì giữ huỷ.
    """

    STALE_ALIVE = {"code": "HD-CRON-1", "id": 7001, "status": 3, "invoiceDelivery": {"status": 7}}
    FRESH_CANCELLED = {"code": "HD-CRON-1", "id": 7001, "status": 2, "invoiceDelivery": {"status": 6}}
    FRESH_ALIVE = {"code": "HD-CRON-1", "id": 7001, "status": 3, "invoiceDelivery": {"status": 7}}

    def setUp(self):
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV Cron 2",
            branch_id="B2",
            retailer="RetailerCron2",
            client_id="cli-cron-2",
            client_secret="secret-cron-2",
        )
        self.channel = Channel.objects.create(code="ch-cron-2", name="Cron Channel 2")
        self.customer = Customer.objects.create(
            full_name="KH Cron 2",
            phone_raw="0911222334",
            phone_normalized="0911222334",
            code="CUST-CRON-2",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            kiotviet_channel=self.kv_channel,
            customer=self.customer,
            name="KH Cron 2",
            phone="0911222334",
            invoice_code="HD-CRON-1",
            invoice_id=7001,
            total_amount="100000",
            status=OrderStatus.CANCELLED,
            invoice_status=2,
        )

    def _run_list_cron(self, *, listed: dict, by_code):
        """`listed` là hoá đơn trong trang danh sách; `by_code` là payload (hoặc
        status_code int) khi cron hỏi lại `/invoices/code/<code>`."""
        calls = []

        def fake_request(*, endpoint, **kwargs):
            calls.append(endpoint)
            if endpoint == "/invoices":
                return _FakeResponse({"data": [listed]})
            if isinstance(by_code, int):
                resp = _FakeResponse({})
                resp.status_code = by_code
                resp.text = "boom"
                return resp
            return _FakeResponse(by_code)

        service = ImportInvoicesService()
        with patch(
            "apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request",
            side_effect=fake_request,
        ), patch(
            "apps.kiotviet.infrastructure.services._import_service_handlers_mixin"
            ".event_bus.publish_on_commit"
        ) as publish:
            service.update_status_phone(
                kv_channel=self.kv_channel,
                branch_ids=["B2"],
                invoice_codes=[],
                dry_run=False,
                do_status=True,
                do_phone=False,
            )
        self.order.refresh_from_db()
        return calls, publish

    def test_stale_alive_snapshot_but_fresh_cancelled_keeps_cancelled(self):
        calls, publish = self._run_list_cron(listed=self.STALE_ALIVE, by_code=self.FRESH_CANCELLED)

        self.assertEqual(int(self.order.status), int(OrderStatus.CANCELLED))
        self.assertEqual(self.order.invoice_status, 2)
        self.assertEqual(calls.count("/invoices/code/HD-CRON-1"), 1)
        publish.assert_not_called()

    def test_fresh_alive_confirms_revival(self):
        """Không sửa quá tay: KiotViet xác nhận hoá đơn sống thật thì đơn được hồi sinh."""
        calls, publish = self._run_list_cron(listed=self.STALE_ALIVE, by_code=self.FRESH_ALIVE)

        self.assertEqual(int(self.order.status), 7)
        self.assertEqual(self.order.invoice_status, 3)
        # Order.save() còn publish OrderSaved (event bus 2026-09-23) — chỉ soi OrderStatusChanged.
        status_events = [c.args[0] for c in publish.call_args_list if isinstance(c.args[0], OrderStatusChanged)]
        self.assertEqual(len(status_events), 1)
        event = status_events[0]
        self.assertEqual((event.old_status, event.new_status), (6, 7))

    def test_fresh_fetch_failure_keeps_cancelled(self):
        """Không xác minh được → fail-closed: giữ huỷ, tick sau thử lại."""
        _, publish = self._run_list_cron(listed=self.STALE_ALIVE, by_code=500)

        self.assertEqual(int(self.order.status), int(OrderStatus.CANCELLED))
        self.assertEqual(self.order.invoice_status, 2)
        publish.assert_not_called()

    def test_non_cancelled_order_does_not_refetch(self):
        """Đơn đang sống đổi trạng thái bình thường: không tốn thêm HTTP."""
        Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.PICKING_UP, invoice_status=3)
        listed = {"code": "HD-CRON-1", "id": 7001, "status": 3, "invoiceDelivery": {"status": 2}}

        calls, _ = self._run_list_cron(listed=listed, by_code=self.FRESH_ALIVE)

        self.assertEqual(int(self.order.status), 2)
        self.assertNotIn("/invoices/code/HD-CRON-1", calls)
