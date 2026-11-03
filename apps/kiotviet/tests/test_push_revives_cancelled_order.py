"""Đẩy lại đơn ĐANG HUỶ thành hoá đơn sống → đơn phải rời CANCELLED ngay.

Sự cố 2026-07-23 (docs/erp-revive-stock-safety-net.md): hoá đơn vừa tạo chưa có
`invoiceDelivery` nên khối cập nhật `Order.status` bị bỏ qua hoàn toàn — đơn kẹt ở
CANCELLED tới khi cron 30' bắt kịp. Hai hậu quả: lookup mã vận đơn trả 409 "Đơn đã hủy"
(kho không xuất được đơn vừa đẩy lại), và cú lật `6 → x` kích hoạt trừ lại tồn bị dời
sang đường khác.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource
from apps.orders.models import Order, OrderStatus

_PUSH = "apps.kiotviet.infrastructure.services.service_actions.push_order_to_kiotviet"
_GET_INVOICE = "apps.kiotviet.infrastructure.services.service_actions.get_invoice_by_code"
_PUBLISH = "apps.common.events.event_bus.publish_on_commit"


class PushRevivesCancelledOrderTests(TestCase):
    def setUp(self):
        self.sales_channel = Channel.objects.create(code="lp-kv-revive", name="KV Revive")
        self.customer = Customer.objects.create(
            full_name="KH Revive",
            phone_raw="0909333555",
            phone_normalized="0909333555",
        )
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV Revive",
            branch_id="B97",
            retailer="RetailerRevive",
            client_id="cli-revive",
            client_secret="secret-revive",
        )

    def _make_order(self, status: int) -> Order:
        return Order.objects.create(
            channel=self.sales_channel,
            kiotviet_channel=self.kv_channel,
            customer=self.customer,
            name="KH Revive",
            phone="0909333555",
            invoice_code="",
            invoice_id=None,
            status=status,
        )

    @staticmethod
    def _push_result(code: str = "HD-REVIVE-1", invoice_id: int = 8300) -> dict:
        return {
            "order_result": {
                "code": "DH-REVIVE-1",
                "invoiceCode": code,
                "invoiceId": invoice_id,
                "invoices": [{"invoiceCode": code, "invoiceId": invoice_id}],
            },
            "make_invoice": False,
        }

    @patch(_PUBLISH)
    @patch(_GET_INVOICE)
    @patch(_PUSH)
    def test_cancelled_order_pushed_to_live_invoice_leaves_cancelled(
        self, mock_push, mock_get_invoice, mock_publish
    ):
        """Hoá đơn "đang xử lý"(3), chưa có vận đơn → đơn về Chờ xử lý + phát sự kiện 6→1."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        order = self._make_order(int(OrderStatus.CANCELLED))
        mock_push.return_value = self._push_result()
        # Hoá đơn vừa tạo: status=3 (đang xử lý), CHƯA có invoiceDelivery.
        mock_get_invoice.return_value = {"code": "HD-REVIVE-1", "status": 3}

        KiotVietService.execute_action(order, KiotVietService.ACTION_PUSH)

        order.refresh_from_db()
        self.assertEqual(order.status, int(OrderStatus.PENDING_PROCESSING))
        # Sự kiện 6 → 1 là thứ subscriber kho cần để trừ lại tồn đã hoàn lúc huỷ.
        mock_publish.assert_any_call(
            OrderStatusChanged(
                order_id=order.id,
                old_status=int(OrderStatus.CANCELLED),
                new_status=int(OrderStatus.PENDING_PROCESSING),
                source=StatusChangeSource.KIOTVIET_ACTION,
            )
        )

    @patch(_PUBLISH)
    @patch(_GET_INVOICE)
    @patch(_PUSH)
    def test_cancelled_order_with_cancelled_invoice_stays_cancelled(
        self, mock_push, mock_get_invoice, mock_publish
    ):
        """Hoá đơn cũng huỷ (status=2) → KHÔNG có bằng chứng đơn sống lại, giữ nguyên."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        order = self._make_order(int(OrderStatus.CANCELLED))
        mock_push.return_value = self._push_result("HD-REVIVE-2", 8301)
        mock_get_invoice.return_value = {"code": "HD-REVIVE-2", "status": 2}

        KiotVietService.execute_action(order, KiotVietService.ACTION_PUSH)

        order.refresh_from_db()
        self.assertEqual(order.status, int(OrderStatus.CANCELLED))

    @patch(_PUBLISH)
    @patch(_GET_INVOICE)
    @patch(_PUSH)
    def test_live_order_without_delivery_status_is_untouched(
        self, mock_push, mock_get_invoice, mock_publish
    ):
        """Đơn KHÔNG huỷ mà hoá đơn chưa có vận đơn → giữ nguyên như trước fix.

        Nhánh mới chỉ được đụng đơn đang CANCELLED; đơn thường phải đi đúng đường cũ,
        nếu không mọi lần đẩy đơn chờ giao đều bị kéo ngược về "Chờ xử lý".
        """
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        order = self._make_order(int(OrderStatus.AWAITING_SHIPMENT))
        mock_push.return_value = self._push_result("HD-REVIVE-3", 8302)
        mock_get_invoice.return_value = {"code": "HD-REVIVE-3", "status": 3}

        KiotVietService.execute_action(order, KiotVietService.ACTION_PUSH)

        order.refresh_from_db()
        self.assertEqual(order.status, int(OrderStatus.AWAITING_SHIPMENT))

    @patch(_PUBLISH)
    @patch(_GET_INVOICE)
    @patch(_PUSH)
    def test_delivery_status_still_wins(self, mock_push, mock_get_invoice, mock_publish):
        """Có `invoiceDelivery.status` thì vẫn dùng nó, không bị nhánh mới chen ngang."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        order = self._make_order(int(OrderStatus.CANCELLED))
        mock_push.return_value = self._push_result("HD-REVIVE-4", 8303)
        mock_get_invoice.return_value = {
            "code": "HD-REVIVE-4",
            "status": 3,
            "invoiceDelivery": {"status": 2},
        }

        KiotVietService.execute_action(order, KiotVietService.ACTION_PUSH)

        order.refresh_from_db()
        self.assertEqual(order.status, int(OrderStatus.IN_DELIVERY))

    @patch(_PUBLISH)
    @patch(_GET_INVOICE)
    @patch(_PUSH)
    def test_unreadable_invoice_leaves_cancelled_order_alone(
        self, mock_push, mock_get_invoice, mock_publish
    ):
        """Đọc hoá đơn lỗi → không có bằng chứng gì, tuyệt đối không tự gỡ huỷ."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        order = self._make_order(int(OrderStatus.CANCELLED))
        mock_push.return_value = self._push_result("HD-REVIVE-5", 8304)
        mock_get_invoice.side_effect = RuntimeError("KiotViet 500")

        KiotVietService.execute_action(order, KiotVietService.ACTION_PUSH)

        order.refresh_from_db()
        self.assertEqual(order.status, int(OrderStatus.CANCELLED))
