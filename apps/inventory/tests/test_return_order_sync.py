"""Đồng bộ trạng thái đơn nguồn khi ghi sổ/huỷ phiếu hoàn (RETURNED ↔ revert) +
đẩy trạng thái sang KiotViet sau commit.

Gộp từ test_return_order_sync.py, test_return_order_status.py.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.models import ReturnEntry
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse

_PUSH = (
    "apps.kiotviet.infrastructure.services.invoice_mutations_push"
    ".update_invoice_delivery_status_by_id"
)


def _make_order(*, status, with_kiotviet=True):
    channel = Channel.objects.create(code="ch-sync", name="Ch Sync")
    customer = Customer.objects.create(
        full_name="Khach S", phone_raw="0900000009",
        phone_normalized="0900000009", code="CUST-S1",
    )
    kwargs = dict(channel=channel, customer=customer, order_code="ORD-S1", status=status)
    if with_kiotviet:
        kv = KiotVietChannel.objects.create(retailer="R-sync")
        kwargs["kiotviet_channel"] = kv
        kwargs["invoice_id"] = 55555
    return Order.objects.create(**kwargs)


class ReturnOrderSyncTests(TestCase):
    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_mark_order_returned_sets_status_and_pushes(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.DELIVERED_SUCCESS)
        with self.captureOnCommitCallbacks(execute=True):
            previous = mark_order_returned(order)

        assert previous == int(OrderStatus.DELIVERED_SUCCESS)
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_called_once()
        assert push.call_args.kwargs["status"] == int(OrderStatus.RETURNED)
        assert push.call_args.kwargs["invoice_id"] == 55555
        assert push.call_args.kwargs["order"].id == order.id
        assert push.call_args.kwargs["channel"] == order.kiotviet_channel

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_mark_order_returned_already_returned_is_noop(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.RETURNED)
        assert mark_order_returned(order) is None
        push.assert_not_called()

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_mark_order_returned_keeps_cancelled_order_cancelled(self, push):
        """Đơn ĐÃ HUỶ mà hàng về kho vẫn là "Đã hủy" — phiếu hoàn chỉ ghi sổ tồn.

        Quyết định 14/09/2026 (đơn 563397): trước đây 6 → 5 rồi cron 30' lật lại 6,
        35 đơn/14 ngày giằng qua lại. Không đổi trạng thái, không đẩy KiotViet (hoá
        đơn đã huỷ, KiotViet từ chối), không phát OrderStatusChanged.
        """
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.CANCELLED)
        with patch("apps.common.events.event_bus.publish_on_commit") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                previous = mark_order_returned(order)

        assert previous is None
        order.refresh_from_db()
        assert order.status == int(OrderStatus.CANCELLED)
        push.assert_not_called()
        publish.assert_not_called()

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_mark_order_returned_no_kiotviet_skips_push(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.DELIVERED_SUCCESS, with_kiotviet=False)
        with self.captureOnCommitCallbacks(execute=True):
            mark_order_returned(order)
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_not_called()

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id",
        side_effect=Exception("KiotViet down"),
    )
    def test_mark_order_returned_push_error_does_not_raise(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.DELIVERED_SUCCESS)
        with self.captureOnCommitCallbacks(execute=True):
            mark_order_returned(order)  # push chạy sau commit, lỗi bị nuốt → không raise
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_called_once()  # callback đã chạy và gọi tới KiotViet (rồi lỗi)
        assert push.call_args.kwargs["status"] == int(OrderStatus.RETURNED)
        assert push.call_args.kwargs["order"].id == order.id

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_revert_order_status_restores_and_pushes(self, push):
        from apps.inventory.application.services.return_order_sync import (
            revert_order_status,
        )

        order = _make_order(status=OrderStatus.RETURNED)
        with self.captureOnCommitCallbacks(execute=True):
            revert_order_status(order, int(OrderStatus.DELIVERED_SUCCESS))
        order.refresh_from_db()
        assert order.status == int(OrderStatus.DELIVERED_SUCCESS)
        assert push.call_args.kwargs["status"] == int(OrderStatus.DELIVERED_SUCCESS)


class PostReturnOrderStatusTests(TestCase):
    """Ghi sổ đơn hoàn (qua luồng quét: tạo với auto_post=true) đồng bộ trạng thái
    đơn nguồn: RETURNED khi có dòng Hàng hoàn, revert khi huỷ, chặn đơn đã hủy."""

    def setUp(self):
        self.user = make_admin("admin_ret_status")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WRS", name="Kho RS")
        self.product = Product.objects.create(name="SP RS", sku="RS-1")

    def _order(self, *, with_kiotviet=True):
        channel = Channel.objects.create(code="ch-rs", name="Ch RS")
        customer = Customer.objects.create(
            full_name="Khach RS", phone_raw="0900001111",
            phone_normalized="0900001111", code="CUST-RS1",
        )
        kwargs = dict(
            channel=channel, customer=customer, order_code="ORD-RS1",
            status=OrderStatus.DELIVERED_SUCCESS, name="Khach RS", phone="0900001111",
        )
        if with_kiotviet:
            kwargs["kiotviet_channel"] = KiotVietChannel.objects.create(retailer="R-rs")
            kwargs["invoice_id"] = 77777
        order = Order.objects.create(**kwargs)
        OrderItem.objects.create(
            order=order, product=self.product, product_name="SP RS",
            sku="RS-1", quantity=3,
        )
        return order

    def _create_posted(self, *, order_id, return_type):
        """Tạo + ghi sổ ngay (auto_post) — đúng luồng quét khớp mã."""
        return self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "source_order_id": order_id,
                "auto_post": True,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 3,
                        "return_type": return_type,
                    }
                ],
            },
            format="json",
        )

    def _sellable_qty(self):
        resp = self.client.get(
            "/api/inventory/stock", {"limit": 50, "offset": 0, "state": "sellable"}
        )
        items = [i for i in resp.json()["items"] if i["product_sku"] == "RS-1"]
        return items[0]["quantity"] if items else 0

    @patch(_PUSH)
    def test_post_delivery_returned_marks_order_returned(self, push):
        order = self._order()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._create_posted(
                order_id=order.id, return_type="delivery_returned"
            )
        assert resp.status_code == 201, resp.json()
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_called_once()
        assert push.call_args.kwargs["status"] == int(OrderStatus.RETURNED)
        assert push.call_args.kwargs["order"].id == order.id
        assert push.call_args.kwargs["invoice_id"] == order.invoice_id

    @patch(_PUSH)
    def test_post_other_type_does_not_touch_order(self, push):
        order = self._order()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._create_posted(order_id=order.id, return_type="warranty")
        assert resp.status_code == 201, resp.json()
        order.refresh_from_db()
        assert order.status == int(OrderStatus.DELIVERED_SUCCESS)
        push.assert_not_called()

    @patch(_PUSH, side_effect=Exception("KiotViet down"))
    def test_post_survives_kiotviet_error(self, push):
        order = self._order()
        # captureOnCommitCallbacks chạy push sau commit; _push_kiotviet nuốt lỗi → không raise.
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._create_posted(
                order_id=order.id, return_type="delivery_returned"
            )
        assert resp.status_code == 201, resp.json()
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_called_once()  # callback đã chạy tới KiotViet (rồi lỗi bị nuốt)
        assert push.call_args.kwargs["status"] == int(OrderStatus.RETURNED)
        assert push.call_args.kwargs["order"].id == order.id

    @patch(_PUSH)
    def test_cancel_reverts_order_status(self, push):
        order = self._order()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._create_posted(
                order_id=order.id, return_type="delivery_returned"
            )
        rid = resp.json()["id"]
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)

        push.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(f"/api/inventory/returns/{rid}/cancel", format="json")
        assert resp.status_code == 200, resp.json()
        order.refresh_from_db()
        assert order.status == int(OrderStatus.DELIVERED_SUCCESS)
        push.assert_called_once()
        assert push.call_args.kwargs["status"] == int(OrderStatus.DELIVERED_SUCCESS)
        assert push.call_args.kwargs["order"].id == order.id

    @patch(_PUSH)
    def test_post_on_cancelled_order_is_blocked_without_inflating_stock(self, push):
        # Đơn ĐÃ HỦY (status 6): tồn đã được hoàn tự động khi hủy (release_for_order), và
        # đơn này còn chưa từng xuất kho (không có SALE) → ghi sổ phiếu trả sẽ cộng tồn
        # KHỐNG (+3 dù hàng chưa bao giờ rời kho). Guard phải chặn (400, rollback cả
        # entry), kèm câu dễ hiểu cho kho.
        order = self._order()
        Order.objects.filter(pk=order.id).update(status=OrderStatus.CANCELLED)

        with self.captureOnCommitCallbacks(execute=True):
            resp = self._create_posted(
                order_id=order.id, return_type="delivery_returned"
            )

        assert resp.status_code == 400, resp.json()
        detail = resp.json()["detail"]
        assert "ORD-RS1" in detail and "đã hủy" in detail
        assert self._sellable_qty() == 0  # không cộng tồn ảo
        assert not ReturnEntry.objects.exists()  # rollback, không để lại nhóm nháp
        order.refresh_from_db()
        assert order.status == int(OrderStatus.CANCELLED)  # không bị kéo về RETURNED
        push.assert_not_called()
