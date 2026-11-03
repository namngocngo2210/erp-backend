"""Đơn HUỶ được hồi sinh (KiotViet huỷ-xuất lại hoá đơn → reattach `.NN`).

Từ 2026-07-29 huỷ đơn KHÔNG còn tự hoàn tồn (spec
docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md §3), nên
với đơn huỷ MỚI không còn gì để "trừ lại" — tồn chưa từng được cộng. Nhánh hồi sinh vẫn
phải chạy vì hai việc khác: trừ lại cho dữ liệu CŨ (đã auto-hoàn trước ngày đó) và đồng
bộ tồn về items hiện tại khi hoá đơn bị sửa dòng hàng.

Các test dưới đây giữ nguyên kết quả CUỐI của từng kịch bản; chỉ số tồn ở giai đoạn
GIỮA (ngay sau khi huỷ) là khác: trước đây về 10, nay giữ nguyên 8.
"""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.common.events import event_bus
from apps.customers.models import Customer
from apps.inventory import subscribers as inventory_subscribers
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import SaleLine, StockSalesService
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


class StockReissueOnReviveTests(TestCase):
    def setUp(self):
        event_bus.clear()
        inventory_subscribers.register()
        self.addCleanup(event_bus.clear)

        self.warehouse = Warehouse.objects.create(code="WH-RVV", name="Kho Revive")
        self.product = Product.objects.create(name="SP Revive", sku="SKU-RVV")
        self.channel = Channel.objects.create(code="ch-revive", name="Revive Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Revive",
            phone_raw="0912345000",
            phone_normalized="0912345000",
            code="CUST-RVV",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Revive",
            phone="0912345000",
            total_amount="100000",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name="SP Revive",
            sku="SKU-RVV",
            quantity=2,
            unit_price="50000",
            line_total="100000",
        )
        OrderAssessment.objects.create(
            order=self.order, delivery_business_status_code="dispatched"
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

    def _qty(self, state="sellable"):
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state=state
        ).first()
        return item.quantity if item else 0

    def _dispatch(self, qty=2):
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=qty)],
        )

    def _cancel(self):
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=int(OrderStatus.IN_DELIVERY),
                new_status=int(OrderStatus.CANCELLED),
            )
        )

    def _revive(self, new_status=OrderStatus.IN_DELIVERY):
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=int(OrderStatus.CANCELLED),
                new_status=int(new_status),
            )
        )

    def test_revive_after_cancel_keeps_stock_deducted(self):
        """Xuất kho → huỷ (KHÔNG hoàn tồn) → hồi sinh: tồn giữ nguyên đã trừ.

        Hàng ở ngoài kho suốt cả ba mốc nên tồn phải là 8 ở cả ba — không có bút toán
        hoàn rồi trừ lại nào cả.
        """
        self._dispatch()
        self.assertEqual(self._qty(), 8)

        self._cancel()
        self.assertEqual(self._qty(), 8, "Huỷ đơn không hoàn tồn: hàng còn ngoài kho")

        self._revive()
        self.assertEqual(self._qty(), 8, "Hồi sinh: tồn vẫn đang trừ, không đụng gì thêm")
        self.assertEqual(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).count(),
            1,
            "Chỉ có đúng lần xuất kho ban đầu",
        )

    def test_revived_order_counts_as_dispatched_again(self):
        """Sau hồi sinh, guard 'đã xuất kho' phải thấy đơn còn outstanding (chống quét xuất lần 2)."""
        from apps.inventory.application.services.stock_sales_service import (
            order_has_outstanding_stock,
        )

        self._dispatch()
        self._cancel()
        # Huỷ không hoàn tồn nữa → outstanding còn nguyên ngay cả khi đang huỷ.
        self.assertTrue(order_has_outstanding_stock(self.order.id))

        self._revive()
        self.assertTrue(order_has_outstanding_stock(self.order.id))

    def test_revive_skips_when_not_dispatched(self):
        """Đơn chưa từng xuất kho (không có SALE) hồi sinh → không trừ tồn."""
        self._cancel()
        self._revive()
        self.assertEqual(self._qty(), 10)
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).exists()
        )

    def test_revive_skips_when_return_note_posted(self):
        """Hàng đã thật sự về kho ĐỦ qua phiếu trả ghi sổ → KHÔNG trừ lại tồn (chỉ log)."""
        self._dispatch()
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[{
                "product_id": self.product.id,
                "quantity": 2,
                "return_type": "delivery_returned",
            }],
            source_order_id=self.order.id,
        )
        self.assertEqual(self._qty(), 10, "Phiếu trả đã cộng lại tồn")
        self._cancel()
        self.assertEqual(self._qty(), 10, "Release no-op vì return đã credit đủ")

        self._revive()
        self.assertEqual(self._qty(), 10, "Hàng đang nằm trong kho — không được trừ lại")

    def test_revive_reissues_only_the_part_still_outside_warehouse(self):
        """Trả hàng MỘT PHẦN: phần đã về kho không trừ lại, phần còn ngoài PHẢI trừ lại.
        Skip trắng khi thấy có phiếu trả sẽ để tồn phồng đúng phần chưa trả."""
        self._dispatch()  # SALE -2 → tồn 8
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[{
                "product_id": self.product.id,
                "quantity": 1,
                "return_type": "delivery_returned",
            }],
            source_order_id=self.order.id,
        )
        self.assertEqual(self._qty(), 9, "Trả 1 → tồn về 9")

        self._cancel()
        self.assertEqual(self._qty(), 9, "Huỷ không hoàn nốt 1 chưa trả — nó còn ngoài kho")

        self._revive()
        self.assertEqual(self._qty(), 9, "Hồi sinh: 1 cái vẫn đang trừ, không đụng thêm")

    def test_revive_reissues_when_order_moved_past_dispatched(self):
        """Đơn đã xuất kho rồi Sale chuyển sang 'Đã nhận' — hàng vẫn ở ngoài kho.
        KiotViet huỷ-xuất lại hoá đơn ĐÃ HOÀN THÀNH chính là ca này (audit vòng 2)."""
        self._dispatch()
        OrderAssessment.objects.filter(order=self.order).update(
            delivery_business_status_code="received"
        )
        self._cancel()
        self.assertEqual(self._qty(), 8)

        self._revive()
        self.assertEqual(self._qty(), 8, "Trạng thái 'received': hàng vẫn ngoài kho")

    def test_revive_does_not_reissue_after_manual_undispatch(self):
        """Un-dispatch (kho quét nhầm, đã hoàn tồn) → huỷ → hồi sinh: KHÔNG trừ lại.

        Reversal của un-dispatch mang note riêng (order_service_commands truyền vào) nên
        không bị tính là 'hoàn tồn do huỷ' — hàng chưa rời kho thì hồi sinh không trừ gì.
        """
        self._dispatch()
        StockSalesService.release_for_order(
            order_id=self.order.id, note="Hoàn tồn do bỏ trạng thái Đã xuất kho"
        )
        OrderAssessment.objects.filter(order=self.order).update(
            delivery_business_status_code="warehouse_processing"
        )
        self.assertEqual(self._qty(), 10)

        self._cancel()  # outstanding = 0 → release no-op, không sinh reversal nào
        self._revive()
        self.assertEqual(self._qty(), 10, "Hàng chưa rời kho — không được trừ")

    def test_reissue_is_idempotent(self):
        """Event hồi sinh đến 2 lần (webhook retry / import + webhook cùng xử lý) chỉ
        được trừ lại MỘT lần."""
        self._dispatch()
        self._cancel()
        self._revive()
        self.assertEqual(self._qty(), 8)

        self._revive()
        self.assertEqual(self._qty(), 8, "Lần hồi sinh thứ 2 phải là no-op")

    def test_cancel_revive_cycle_twice(self):
        """Huỷ → hồi sinh → huỷ lại → hồi sinh lại: không trôi tồn qua các chu kỳ."""
        self._dispatch()
        self._cancel()
        self._revive()
        self.assertEqual(self._qty(), 8)

        self._cancel()
        self.assertEqual(self._qty(), 8)
        self._revive()
        self.assertEqual(self._qty(), 8, "Chu kỳ thứ 2 vẫn giữ đúng 2 cái ngoài kho")

    def test_revive_to_returned_status_reissues(self):
        """Hồi sinh sang RETURNED(5) vẫn trừ lại tồn: RETURNED không auto-release, hàng
        chỉ về kho qua phiếu trả (quét vật lý) nên đơn vẫn đang giữ hàng ngoài kho."""
        self._dispatch()
        self._cancel()
        self._revive(new_status=OrderStatus.RETURNED)
        self.assertEqual(self._qty(), 8, "RETURNED vẫn là 'đơn sống' về mặt tồn đã xuất")

    def test_revive_after_invoice_items_changed_resyncs_to_new_items(self):
        """Hoá đơn bị SỬA dòng hàng khi hồi sinh (chính là lý do KiotViet huỷ-xuất lại):
        tồn phải bám items MỚI, không phải items lúc xuất kho.

        Trừ lại theo sổ là đúng cho SKU cũ, nhưng nếu dừng ở đó thì SKU cũ bị trừ vĩnh
        viễn dù không còn trong đơn, còn SKU mới không bao giờ bị trừ — tồn sai 2 chiều
        và phiếu trả sau này bị `_guard_double_credit` chặn (audit 14/07 vòng 3).
        """
        other = Product.objects.create(name="SP Khac", sku="SKU-OTHER")
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=other.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

        self._dispatch()  # xuất 2 × SKU-RVV → còn 8
        self._cancel()  # không hoàn tồn → vẫn 8
        self.assertEqual(self._qty(), 8)

        # KiotViet xuất lại hoá đơn với dòng hàng khác: import/webhook ghi đè OrderItem.
        self.order.items.all().delete()
        OrderItem.objects.create(
            order=self.order,
            product=other,
            product_name="SP Khac",
            sku="SKU-OTHER",
            quantity=3,
            unit_price="50000",
            line_total="150000",
        )
        self._revive()

        self.assertEqual(self._qty(), 10, "SKU cũ không còn trong đơn → không được giữ trừ")
        other_qty = StockItem.objects.get(
            warehouse=self.warehouse, product=other, state="sellable"
        ).quantity
        self.assertEqual(other_qty, 7, "SKU mới phải bị trừ đúng 3")
