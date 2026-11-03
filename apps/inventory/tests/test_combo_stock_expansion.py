"""Nở SKU combo thành mã lẻ khi GHI SỔ tồn kho.

KiotViet trừ tồn combo vào các mã lẻ theo công thức; CRM trước đây trừ vào chính
SKU combo (mã không bao giờ được nhập kho) nên tồn mã lẻ cao hơn thực tế còn tồn
SKU combo âm vĩnh viễn. Xem docs/combo-stock-expansion-2026-08-20.md.

Nở nằm BÊN TRONG `issue_for_order` / `resync_for_order` — mọi caller đều đúng theo
mặc định. Các luồng hoàn/huỷ đọc ngược từ sổ (`outstanding_by_key`) nên tự khớp mã
lẻ, phần đó test ở đây để khoá hành vi lại.
"""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.models import Order
from apps.catalog.models import Product, ProductComponent
from apps.warehouses.models import Warehouse


class ComboStockExpansionTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-CB", name="Kho Combo")
        self.channel = Channel.objects.create(code="ch-combo", name="Combo Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Combo",
            phone_raw="0913000001",
            phone_normalized="0913000001",
            code="CUST-CB",
        )
        # Combo BNC02+NC01 = 1 bồn ngâm chân + 1 gói thảo dược.
        self.combo = Product.objects.create(name="Combo bồn + thảo dược", sku="CB-BNC+NC")
        self.part_a = Product.objects.create(name="Bồn ngâm chân", sku="CB-BNC")
        self.part_b = Product.objects.create(name="Thảo dược ngâm chân", sku="CB-NC")
        ProductComponent.objects.create(parent=self.combo, component=self.part_a, quantity=1)
        ProductComponent.objects.create(parent=self.combo, component=self.part_b, quantity=1)
        for product in (self.part_a, self.part_b):
            StockCommandService.record_movement(
                warehouse_id=self.warehouse.id,
                product_id=product.id,
                qty_delta=10,
                movement_type=MovementType.RECEIPT,
            )

    def _order(self, code: str) -> Order:
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Combo",
            phone="0913000001",
            total_amount="100000",
            order_code=code,
            fulfillment_warehouse=self.warehouse,
        )

    def _qty(self, product: Product) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def test_ban_combo_tru_ton_vao_ma_le(self):
        """Bán 1 combo → trừ mã lẻ, KHÔNG đụng tồn của chính SKU combo."""
        order = self._order("CB-ORD-1")
        StockSalesService.issue_for_order(
            order_id=order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.combo.id, quantity=1)],
        )

        self.assertEqual(self._qty(self.part_a), 9)
        self.assertEqual(self._qty(self.part_b), 9)
        self.assertEqual(self._qty(self.combo), 0)
        self.assertFalse(
            StockMovement.objects.filter(
                reference_id=order.id, product_id=self.combo.id
            ).exists(),
            "Không được ghi bút toán nào vào chính SKU combo",
        )

    def test_nhan_so_luong_theo_cong_thuc_combo(self):
        """Combo khai `quantity=3` và bán 2 combo → trừ 6 đơn vị mã lẻ."""
        bundle = Product.objects.create(name="Combo 3 khăn", sku="CB-3KHAN")
        towel = Product.objects.create(name="Khăn lau", sku="CB-KHAN")
        ProductComponent.objects.create(parent=bundle, component=towel, quantity=3)
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=towel.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        order = self._order("CB-ORD-2")

        StockSalesService.issue_for_order(
            order_id=order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=bundle.id, quantity=2)],
        )

        self.assertEqual(self._qty(towel), 4)

    def test_combo_chua_khai_thanh_phan_van_tru_chinh_no(self):
        """Fallback: 118/121 SKU combo trên prod chưa khai thành phần — không được
        im lặng đánh mất bút toán, vẫn trừ vào chính SKU đó như trước."""
        undeclared = Product.objects.create(name="Combo chưa khai", sku="CB-CHUAKHAI")
        order = self._order("CB-ORD-3")

        StockSalesService.issue_for_order(
            order_id=order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=undeclared.id, quantity=1)],
        )

        self.assertEqual(self._qty(undeclared), -1)

    def test_don_vua_mua_combo_vua_mua_le_thi_cong_don(self):
        """Cùng đơn có combo chứa mã A và một dòng mã A bán lẻ → gộp thành MỘT
        bút toán, không phải hai dòng rời."""
        order = self._order("CB-ORD-4")

        StockSalesService.issue_for_order(
            order_id=order.id,
            warehouse_id=self.warehouse.id,
            lines=[
                SaleLine(product_id=self.combo.id, quantity=1),
                SaleLine(product_id=self.part_a.id, quantity=2),
            ],
        )

        self.assertEqual(self._qty(self.part_a), 7)
        self.assertEqual(self._qty(self.part_b), 9)
        self.assertEqual(
            StockMovement.objects.filter(
                reference_id=order.id, product_id=self.part_a.id
            ).count(),
            1,
        )

    def test_huy_don_combo_hoan_dung_ma_le(self):
        """Luồng hoàn đọc ngược từ sổ nên tự khớp mã lẻ — khoá hành vi lại."""
        order = self._order("CB-ORD-5")
        StockSalesService.issue_for_order(
            order_id=order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.combo.id, quantity=1)],
        )

        StockSalesService.release_for_order(order_id=order.id, note="Hoàn tồn do huỷ đơn")

        self.assertEqual(self._qty(self.part_a), 10)
        self.assertEqual(self._qty(self.part_b), 10)

    def test_resync_khi_sua_items_sau_xuat_kho(self):
        """Sửa số lượng combo sau khi đã xuất → đồng bộ chênh lệch trên mã lẻ."""
        order = self._order("CB-ORD-6")
        StockSalesService.issue_for_order(
            order_id=order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.combo.id, quantity=2)],
        )
        self.assertEqual(self._qty(self.part_a), 8)

        StockSalesService.resync_for_order(
            order_id=order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.combo.id, quantity=1)],
        )

        self.assertEqual(self._qty(self.part_a), 9)
        self.assertEqual(self._qty(self.part_b), 9)
