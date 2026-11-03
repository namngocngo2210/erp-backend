"""Unit tests cho vòng đời trừ/hoàn tồn theo đơn (net-outstanding).

Bao phủ các fix hardening:
- issue lại được sau khi đã release (chu kỳ huỷ → mở lại → xuất lại);
- release trừ credit nhóm đơn hoàn (ReturnEntry) đã ghi sổ (không cộng tồn 2 lần);
- post_entry của đơn đã auto-hoàn tồn (dữ liệu cũ) ghi kèm bút toán thu hồi;
- resync_for_order đồng bộ tồn khi items đổi sau xuất kho.
"""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    AUTO_RELEASE_RECLAIM_NOTE,
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


class StockSalesCycleTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-CYC", name="Kho Cycle")
        self.product = Product.objects.create(name="SP Cycle", sku="SKU-CYC")
        self.channel = Channel.objects.create(code="ch-cycle", name="Cycle Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Cycle",
            phone_raw="0912345678",
            phone_normalized="0912345678",
            code="CUST-CYC",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Cycle",
            phone="0912345678",
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
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

    def _issue(self, qty=2):
        return StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=qty)],
        )

    def _lines(self, qty, return_type="delivery_returned"):
        return [{
            "product_id": self.product.id,
            "quantity": qty,
            "return_type": return_type,
        }]

    def _draft_return(self, qty, return_type="delivery_returned"):
        return ReturnEntryService.create_entry(
            warehouse_id=self.warehouse.id,
            lines=self._lines(qty, return_type),
            source_order_id=self.order.id,
        )

    def _post_return(self, qty, return_type="delivery_returned"):
        return ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=self._lines(qty, return_type),
            source_order_id=self.order.id,
        )

    def test_reissue_after_full_release(self):
        """Xuất → hoàn → xuất lại phải trừ tồn lần nữa (guard cũ 'nuốt' lần 2)."""
        self._issue(2)
        self.assertEqual(self._qty(), 8)
        StockSalesService.release_for_order(order_id=self.order.id)
        self.assertEqual(self._qty(), 10)

        self._issue(2)
        self.assertEqual(self._qty(), 8, "Lần xuất thứ 2 sau khi hoàn phải trừ tồn")
        self.assertEqual(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).count(),
            2,
        )

    def test_issue_idempotent_while_outstanding(self):
        self._issue(2)
        self._issue(2)
        self.assertEqual(self._qty(), 8)

    def test_release_idempotent(self):
        self._issue(2)
        StockSalesService.release_for_order(order_id=self.order.id)
        StockSalesService.release_for_order(order_id=self.order.id)
        self.assertEqual(self._qty(), 10, "Release lần 2 phải là no-op")

    def test_release_skips_posted_return_credit(self):
        """Hàng hoàn đã quét nhập (ReturnEntry posted) rồi đơn mới bị huỷ →
        release không được cộng tồn thêm lần nữa."""
        self._issue(2)
        self._post_return(2)
        self.assertEqual(self._qty(), 10, "Nhóm đơn hoàn đã cộng lại đủ 2")

        StockSalesService.release_for_order(order_id=self.order.id)
        self.assertEqual(self._qty(), 10, "Release phải no-op vì return đã credit đủ")
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE_REVERSAL,
            ).exists()
        )

    def test_release_restores_only_unreturned_part(self):
        self._issue(2)
        self._post_return(1)
        self.assertEqual(self._qty(), 9)

        StockSalesService.release_for_order(order_id=self.order.id)
        self.assertEqual(self._qty(), 10, "Chỉ hoàn phần 1 chưa được return credit")
        reversal = StockMovement.objects.get(
            reference_type="order",
            reference_id=self.order.id,
            movement_type=MovementType.SALE_REVERSAL,
        )
        self.assertEqual(reversal.qty_delta, 1)

    def test_release_counts_warranty_return_as_credit(self):
        """Return warranty cũng là 1 đơn vị vật lý đã về kho → release không
        cộng lại phần đó vào sellable nữa."""
        self._issue(2)
        self._post_return(2, return_type="warranty")
        self.assertEqual(self._qty("warranty"), 2)

        StockSalesService.release_for_order(order_id=self.order.id)
        self.assertEqual(self._qty(), 8, "Sellable giữ nguyên — không double credit")

    def test_post_return_after_legacy_release_credits_once(self):
        """Đơn huỷ CŨ đã auto-hoàn tồn, hàng nay mới về: phiếu hoàn được nhận nhưng
        tồn KHÔNG cộng lần hai.

        `release_for_order` không note = dấu vết auto-hoàn khi huỷ của dữ liệu trước
        2026-07-29. `post_entry` ghi kèm bút toán thu hồi nên tồn ròng đứng yên
        (spec §6). Trước đây ca này bị chặn thẳng, mã vận đơn kẹt không ghi nhận được.
        """
        self._issue(2)
        StockSalesService.release_for_order(order_id=self.order.id)
        self.assertEqual(self._qty(), 10)

        entry = self._draft_return(2)
        ReturnEntryService.post_entry(entry_id=entry.id)

        self.assertEqual(self._qty(), 10, "return_in +2 và thu hồi -2 → tồn không đổi")
        self.assertTrue(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
                note=AUTO_RELEASE_RECLAIM_NOTE,
            ).exists()
        )

    def test_post_return_blocked_beyond_issued(self):
        """Tổng return theo đơn không vượt số đã xuất (phiếu thứ 2 vượt → chặn)."""
        self._issue(2)
        self._post_return(2, return_type="warranty")

        entry = self._draft_return(1, return_type="warranty")
        with self.assertRaises(ValueError):
            ReturnEntryService.post_entry(entry_id=entry.id)

    def test_post_return_without_source_order_not_guarded(self):
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=self._lines(3, return_type="warranty"),
        )
        self.assertEqual(self._qty("warranty"), 3)

    def test_resync_noop_when_not_issued(self):
        StockSalesService.resync_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=5)],
        )
        self.assertEqual(self._qty(), 10)

    def test_resync_decrease_reverses_diff(self):
        self._issue(3)
        self.assertEqual(self._qty(), 7)
        StockSalesService.resync_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=1)],
        )
        self.assertEqual(self._qty(), 9, "Giảm items 3→1 phải hoàn 2")

    def test_resync_increase_deducts_more(self):
        self._issue(1)
        StockSalesService.resync_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )
        self.assertEqual(self._qty(), 7, "Tăng items 1→3 phải trừ thêm 2")

    def test_resync_new_product_added(self):
        other = Product.objects.create(name="SP Cycle 2", sku="SKU-CYC-2")
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=other.id,
            qty_delta=5,
            movement_type=MovementType.RECEIPT,
        )
        self._issue(2)
        StockSalesService.resync_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[
                SaleLine(product_id=self.product.id, quantity=2),
                SaleLine(product_id=other.id, quantity=1),
            ],
        )
        self.assertEqual(self._qty(), 8, "Sản phẩm cũ giữ nguyên")
        other_item = StockItem.objects.get(warehouse=self.warehouse, product=other)
        self.assertEqual(other_item.quantity, 4, "Sản phẩm mới thêm phải bị trừ")

    def test_resync_respects_return_credit(self):
        """Phần đã có nhóm đơn hoàn credit thì resync không hoàn thêm."""
        self._issue(3)
        self._post_return(2)
        self.assertEqual(self._qty(), 9)  # 10 - 3 + 2

        StockSalesService.resync_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=1)],
        )
        # deducted=3, target=1, returned=2 → chỉ hoàn thêm 3 - max(1,2) = 1
        self.assertEqual(self._qty(), 10)

    def test_issue_merges_duplicate_product_lines(self):
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[
                SaleLine(product_id=self.product.id, quantity=1),
                SaleLine(product_id=self.product.id, quantity=2),
            ],
        )
        self.assertEqual(self._qty(), 7)
        mv = StockMovement.objects.get(
            reference_type="order",
            reference_id=self.order.id,
            movement_type=MovementType.SALE,
        )
        self.assertEqual(mv.qty_delta, -3, "2 dòng cùng sản phẩm gộp thành 1 movement")
