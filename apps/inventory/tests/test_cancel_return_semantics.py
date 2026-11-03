"""Ngữ nghĩa huỷ đơn × nhập hoàn: giữ outstanding khi huỷ, cộng tồn đúng một lần,
và đường dữ liệu cũ (đơn huỷ trước 2026-07-29 đã auto-hoàn tồn).

Gộp từ test_cancel_keeps_outstanding.py, test_return_cancelled_order_no_double_credit.py,
test_return_after_cancel_legacy.py. Cả ba publish OrderStatusChanged qua event_bus
(subscriber inventory đăng ký ở setUp) — không đụng tới event_bus.clear().
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.common.events import event_bus
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    AUTO_RELEASE_RECLAIM_NOTE,
    CANCEL_RELEASE_NOTE,
    SaleLine,
    StockSalesService,
    has_pending_cancel_release,
    outstanding_by_key,
)
from apps.inventory.models import (
    MovementType,
    ReturnEntry,
    ReturnNote,
    StockItem,
    StockMovement,
)
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


class CancelKeepsOutstandingTests(TestCase):
    """Huỷ đơn ĐÃ xuất kho: tồn KHÔNG được tự cộng lại — hàng còn ngoài kho.

    Tồn chỉ cộng khi hàng thật về (phiếu hoàn ghi sổ). Xem
    docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md §3.
    """

    def setUp(self):
        from apps.inventory import subscribers

        subscribers.register()
        self.warehouse = Warehouse.objects.create(code="WH-CKO", name="Kho CKO")
        self.product = Product.objects.create(name="SP CKO", sku="SKU-CKO1")
        self.channel = Channel.objects.create(code="ch-cko", name="CKO Channel")
        self.customer = Customer.objects.create(
            full_name="Khach CKO",
            phone_raw="0912345701",
            phone_normalized="0912345701",
            code="CUST-CKO",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-CKO-1",
            shipping_code="TRACK-CKO-1",
            name="Khach CKO",
            phone="0912345701",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        # Đơn thật luôn có dòng hàng; thiếu items thì resync lúc hồi sinh hiểu là
        # "đơn không còn hàng nào" và hoàn sạch tồn — không phản ánh ca thật.
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name="SP CKO",
            sku="SKU-CKO1",
            quantity=2,
            unit_price=50000,
            line_total=100000,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

    def _sellable(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def _dispatch(self, qty: int = 2) -> None:
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=qty)],
        )

    def _cancel(self) -> None:
        previous = int(self.order.status)
        Order.objects.filter(pk=self.order.id).update(
            status=OrderStatus.CANCELLED, invoice_status=2
        )
        self.order.refresh_from_db()
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=previous,
                new_status=int(OrderStatus.CANCELLED),
            )
        )

    def test_cancel_after_dispatch_does_not_credit_stock(self):
        self._dispatch(2)
        self.assertEqual(self._sellable(), 8)

        self._cancel()

        # Hàng vẫn ngoài kho → tồn giữ nguyên 8, không có bút toán đảo nào.
        self.assertEqual(self._sellable(), 8)
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE_REVERSAL,
            ).exists()
        )
        # Outstanding còn nguyên → phiếu hoàn sau này cộng tồn được.
        self.assertEqual(
            outstanding_by_key(self.order.id),
            {(self.warehouse.id, self.product.id): 2},
        )

    def test_cancel_before_dispatch_is_still_noop(self):
        self._cancel()
        self.assertEqual(self._sellable(), 10)
        self.assertEqual(outstanding_by_key(self.order.id), {})

    def test_revive_without_cancel_release_does_not_deduct_again(self):
        """Đơn huỷ (không release) được hồi sinh: KHÔNG trừ lại — tồn chưa từng được cộng."""
        self._dispatch(2)
        self._cancel()
        self.assertEqual(self._sellable(), 8)

        Order.objects.filter(pk=self.order.id).update(status=OrderStatus.IN_DELIVERY)
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=int(OrderStatus.CANCELLED),
                new_status=int(OrderStatus.IN_DELIVERY),
            )
        )

        self.assertEqual(self._sellable(), 8)
        self.assertEqual(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).count(),
            1,
        )


class ReturnOnCancelledOrderTests(TestCase):
    """Nhập hoàn đơn ĐÃ HỦY: tồn cộng đúng MỘT lần, và không cộng tồn ảo.

    Chuỗi thật (từ 2026-07-29): đơn xuất kho (SALE) → bị hủy (webhook publish
    OrderStatusChanged new=6) → outstanding GIỮ NGUYÊN vì hàng còn ngoài kho. Hàng thu hồi
    về, nhân viên quét "Nhập hoàn" → lúc này tồn mới được cộng. Quét lần hai vẫn bị chặn.

    Đơn hủy CHƯA từng xuất kho thì không có gì để hoàn — chặn để không sinh tồn ảo.
    """

    def setUp(self):
        # Subscriber inventory (release_for_order khi status=6) phải được đăng ký.
        from apps.inventory import subscribers

        subscribers.register()

        self.warehouse = Warehouse.objects.create(code="WH-CNC", name="Kho CNC")
        self.product = Product.objects.create(name="SP CNC", sku="SKU-CNC1")
        self.channel = Channel.objects.create(code="ch-cnc", name="CNC Channel")
        self.customer = Customer.objects.create(
            full_name="Khach CNC",
            phone_raw="0912345691",
            phone_normalized="0912345691",
            code="CUST-CNC",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-CNC-1",
            shipping_code="TRACK-CNC-1",
            name="Khach CNC",
            phone="0912345691",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

    def _sellable(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def _dispatch(self, qty: int = 2) -> None:
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=qty)],
        )

    def _cancel(self) -> None:
        """Hủy đơn đúng đường thật: đổi status + publish OrderStatusChanged(new=6)."""
        previous = int(self.order.status)
        Order.objects.filter(pk=self.order.id).update(
            status=OrderStatus.CANCELLED, invoice_status=2
        )
        self.order.refresh_from_db()
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=previous,
                new_status=int(OrderStatus.CANCELLED),
            )
        )

    def _scan_return(self, qty: int = 2) -> ReturnEntry:
        """Đúng lời gọi của luồng quét nhập hoàn (`_complete_return` / auto-post)."""
        return ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": qty,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )

    def test_dispatched_then_cancelled_then_return_scan_credits_once(self):
        """Đã xuất kho → hủy (KHÔNG tự hoàn tồn) → quét nhập hoàn: tồn cộng đúng một lần."""
        self._dispatch(2)
        self.assertEqual(self._sellable(), 8)  # trừ tồn khi xuất kho

        self._cancel()
        self.assertEqual(self._sellable(), 8)  # hàng còn ngoài kho, chưa cộng gì

        entry = self._scan_return(2)

        self.assertEqual(entry.status, "posted")
        self.assertEqual(self._sellable(), 10)  # về đủ, không phồng thành 12

    def test_posting_return_does_not_trigger_revive_deduction(self):
        """Bẫy 6→5: ghi sổ phiếu hoàn đặt đơn = RETURNED. Subscriber KHÔNG được hiểu
        đó là 'đơn hủy hồi sinh' rồi chạy reissue/resync trừ lại tồn (spec §4)."""
        self._dispatch(2)
        self._cancel()
        self._scan_return(2)

        self.assertEqual(self._sellable(), 10)
        self.assertEqual(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).count(),
            1,
            "Chỉ có đúng lần xuất kho ban đầu — không có bút toán trừ lại nào",
        )

    def test_return_scan_keeps_cancelled_status(self):
        """Đơn đã huỷ, hàng về kho: tồn cộng lại nhưng đơn VẪN "Đã hủy" (quyết định
        14/09/2026). Nhóm đơn không lưu order_status_before → huỷ phiếu sau đó không
        có gì để revert."""
        self._dispatch(2)
        self._cancel()

        with patch("apps.common.events.event_bus.publish_on_commit") as publish:
            entry = self._scan_return(2)

        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), int(OrderStatus.CANCELLED))
        self.assertIsNone(entry.order_status_before)
        self.assertEqual(self._sellable(), 10)
        publish.assert_not_called()

    def test_return_scan_twice_is_still_blocked(self):
        """Không hồi quy: quét nhập hoàn lần hai vẫn bị chặn (chống cộng tồn hai lần)."""
        self._dispatch(2)
        self._cancel()
        self._scan_return(2)

        with self.assertRaises(ValueError):
            ReturnEntryService.create_and_post_entry(
                warehouse_id=self.warehouse.id,
                lines=[
                    {
                        "product_id": self.product.id,
                        "quantity": 2,
                        "return_type": "delivery_returned",
                    }
                ],
                source_order_id=self.order.id,
                shipping_code="TRACK-CNC-1-B",
            )
        self.assertEqual(self._sellable(), 10)

    def test_cancelled_before_dispatch_return_scan_does_not_inflate_stock(self):
        """Đơn hủy CHƯA từng xuất kho: hàng chưa rời kho → quét nhập hoàn KHÔNG cộng tồn ảo."""
        self._cancel()
        self.assertEqual(self._sellable(), 10)

        with self.assertRaises(ValueError) as ctx:
            self._scan_return(2)

        self.assertIn("đã hủy", str(ctx.exception))
        self.assertEqual(self._sellable(), 10)  # trước fix: phồng lên 12 (tồn ảo)
        self.assertEqual(ReturnEntry.objects.count(), 0)
        self.assertEqual(ReturnNote.objects.count(), 0)

    def test_live_order_return_scan_still_credits_stock(self):
        """Không hồi quy: đơn KHÔNG hủy (giao thất bại, hoàn về) vẫn cộng tồn như cũ."""
        self._dispatch(2)
        entry = self._scan_return(2)
        self.assertEqual(entry.status, "posted")
        self.assertTrue(entry.return_note.code.startswith("PTH-"))
        self.assertEqual(self._sellable(), 10)  # -2 khi xuất, +2 khi nhập hoàn


class ReturnAfterLegacyCancelTests(TestCase):
    """Đơn huỷ TRƯỚC 2026-07-29 (đã auto-hoàn tồn) mà hàng nay mới về.

    Quét nhập hoàn vẫn nhận, nhưng ghi kèm bút toán thu hồi để tồn ròng không đổi —
    phần tồn đó đã được cộng từ lúc huỷ. Xem spec §6.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-LEG", name="Kho LEG")
        self.product = Product.objects.create(name="SP LEG", sku="SKU-LEG1")
        self.channel = Channel.objects.create(code="ch-leg", name="LEG Channel")
        self.customer = Customer.objects.create(
            full_name="Khach LEG",
            phone_raw="0912345703",
            phone_normalized="0912345703",
            code="CUST-LEG",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-LEG-1",
            shipping_code="TRACK-LEG-1",
            name="Khach LEG",
            phone="0912345703",
            status=OrderStatus.CANCELLED,
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name="SP LEG",
            sku="SKU-LEG1",
            quantity=2,
            unit_price=50000,
            line_total=100000,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        # Dựng đúng dấu vết dữ liệu cũ: xuất kho rồi auto-hoàn khi huỷ.
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=2)],
        )
        StockSalesService.release_for_order(
            order_id=self.order.id, note=CANCEL_RELEASE_NOTE
        )

    def _sellable(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def _scan_return(self, qty: int = 2):
        return ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": qty,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )

    def test_legacy_cancelled_order_accepts_return_without_double_credit(self):
        self.assertEqual(self._sellable(), 10)  # đã được cộng từ lúc huỷ

        entry = self._scan_return(2)

        self.assertEqual(entry.status, "posted")
        # Hàng về được ghi nhận, nhưng tồn KHÔNG phồng: return_in +2 và thu hồi -2.
        self.assertEqual(self._sellable(), 10)
        reclaim = StockMovement.objects.filter(
            reference_type="order",
            reference_id=self.order.id,
            movement_type=MovementType.SALE,
            note=AUTO_RELEASE_RECLAIM_NOTE,
        )
        self.assertEqual(reclaim.count(), 1)
        self.assertEqual(reclaim.first().qty_delta, -2)
        self.assertTrue(
            StockMovement.objects.filter(
                reference_type="return_entry",
                reference_id=entry.id,
                movement_type=MovementType.RETURN_IN,
                qty_delta=2,
            ).exists()
        )

    def test_reclaim_clears_pending_so_revive_does_not_deduct_twice(self):
        self._scan_return(2)

        # Phần auto-hoàn đã được thu hồi → hồi sinh không còn gì để trừ lại.
        self.assertFalse(has_pending_cancel_release(self.order.id))
        StockSalesService.reissue_after_cancel_release(order_id=self.order.id)
        self.assertEqual(self._sellable(), 10)
