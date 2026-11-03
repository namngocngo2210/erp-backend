"""Backfill chuyển sổ tồn từ SKU combo sang mã lẻ (migration 0038).

Xem docs/combo-stock-expansion-2026-08-20.md. Dựng lại đúng trạng thái dữ liệu cũ
trên prod: bút toán SALE nằm trên chính SKU combo, mã lẻ chưa bị trừ.
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.combo_stock_backfill import (
    backfill_combo_stock,
    combo_outstanding_report,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    ADMIN_SALE_NOTES,
    COMBO_BACKFILL_ISSUE_NOTE,
    real_dispatch_occurred_at,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.models import Order
from apps.catalog.models import Product, ProductComponent
from apps.warehouses.models import Warehouse


class ComboStockBackfillTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-BF", name="Kho Backfill")
        self.channel = Channel.objects.create(code="ch-bf", name="Backfill Channel")
        self.customer = Customer.objects.create(
            full_name="Khach BF",
            phone_raw="0913000002",
            phone_normalized="0913000002",
            code="CUST-BF",
        )
        self.combo = Product.objects.create(name="Combo BF", sku="BF-COMBO")
        self.part_a = Product.objects.create(name="Phần A", sku="BF-A")
        self.part_b = Product.objects.create(name="Phần B", sku="BF-B")
        ProductComponent.objects.create(parent=self.combo, component=self.part_a, quantity=1)
        ProductComponent.objects.create(parent=self.combo, component=self.part_b, quantity=2)
        for product in (self.part_a, self.part_b):
            StockCommandService.record_movement(
                warehouse_id=self.warehouse.id,
                product_id=product.id,
                qty_delta=10,
                movement_type=MovementType.RECEIPT,
            )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach BF",
            phone="0913000002",
            total_amount="100000",
            order_code="BF-ORD-1",
            fulfillment_warehouse=self.warehouse,
        )

    def _legacy_sale(self, quantity: int = 1, occurred_at=None) -> None:
        """Bút toán kiểu CŨ: trừ thẳng vào SKU combo."""
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.combo.id,
            qty_delta=-quantity,
            movement_type=MovementType.SALE,
            reference_type="order",
            reference_id=self.order.id,
            occurred_at=occurred_at,
        )

    def _qty(self, product: Product) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def test_chuyen_ton_tu_combo_sang_ma_le(self):
        self._legacy_sale()
        self.assertEqual(self._qty(self.combo), -1)

        stats = backfill_combo_stock()

        self.assertEqual(self._qty(self.combo), 0, "SKU combo phải hết âm")
        self.assertEqual(self._qty(self.part_a), 9)
        self.assertEqual(self._qty(self.part_b), 8, "quantity=2 nên trừ 2 đơn vị")
        self.assertEqual(stats["orders"], 1)
        self.assertEqual(stats["moved"], 1)

    def test_idempotent_chay_lai_khong_tru_them(self):
        self._legacy_sale()
        backfill_combo_stock()

        second = backfill_combo_stock()

        self.assertEqual(second["moved"], 0)
        self.assertEqual(self._qty(self.part_a), 9)
        self.assertEqual(self._qty(self.part_b), 8)
        self.assertEqual(self._qty(self.combo), 0)

    def test_dry_run_khong_ghi_gi(self):
        self._legacy_sale()
        before = StockMovement.objects.count()

        stats = backfill_combo_stock(dry_run=True)

        self.assertEqual(stats["moved"], 1)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(self._qty(self.combo), -1)

    def test_bo_qua_don_da_hoan_het(self):
        """Đơn đã hoàn hết: tồn combo về 0, mã lẻ chưa từng bị trừ — không có gì để sửa."""
        self._legacy_sale()
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.combo.id,
            qty_delta=1,
            movement_type=MovementType.SALE_REVERSAL,
            reference_type="order",
            reference_id=self.order.id,
        )

        stats = backfill_combo_stock()

        self.assertEqual(stats["moved"], 0)
        self.assertEqual(self._qty(self.part_a), 10)
        self.assertEqual(self._qty(self.part_b), 10)

    def test_but_toan_backfill_giu_nguyen_ngay_xuat_goc(self):
        """Chuyển sổ KHÔNG được dời ngày hàng rời kho sang ngày chạy backfill.

        Cách bảo vệ là `occurred_at` của bút toán gốc, KHÔNG phải xếp note vào
        `ADMIN_SALE_NOTES` — xếp vào đó thì mã lẻ biến mất khỏi sản lượng xuất
        trên màn Lịch sử nhập xuất (ca prod 20/08). Xem
        docs/combo-stock-expansion-2026-08-20.md.
        """
        goc = timezone.now() - timedelta(days=5)
        self._legacy_sale(occurred_at=goc)

        backfill_combo_stock()

        self.assertNotIn(COMBO_BACKFILL_ISSUE_NOTE, ADMIN_SALE_NOTES)
        moved = StockMovement.objects.filter(
            reference_id=self.order.id,
            movement_type=MovementType.SALE,
            note=COMBO_BACKFILL_ISSUE_NOTE,
        )
        self.assertEqual(moved.count(), 2)
        for movement in moved:
            self.assertEqual(movement.occurred_at, goc)
        self.assertEqual(real_dispatch_occurred_at(self.order.id), goc)

    def test_bo_qua_phan_da_hoan_ve_kho_duoi_sku_combo(self):
        """Ca có thật trên prod (đơn 548336): hàng combo đã hoàn về kho và được nhập
        lại dưới CHÍNH SKU combo. Phiếu hoàn ghi `reference_type='return_entry'` nên
        outstanding của đơn vẫn còn — chuyển sổ phần đó sẽ trừ mã lẻ trong khi 1 bộ
        combo vẫn nằm trong kho, tức tạo tồn ảo."""
        from datetime import date

        from apps.inventory.models import (
            ReturnEntry,
            ReturnNote,
            ReturnNoteLine,
            ReturnStatus,
            ReturnType,
        )

        self._legacy_sale()
        note = ReturnNote.objects.create(
            code="PH-BF-1", warehouse=self.warehouse, doc_date=date(2026, 8, 20)
        )
        entry = ReturnEntry.objects.create(
            return_note=note,
            source_order=self.order,
            status=ReturnStatus.POSTED,
        )
        ReturnNoteLine.objects.create(
            entry=entry,
            product=self.combo,
            quantity=1,
            return_type=ReturnType.DELIVERY_RETURNED,
        )

        stats = backfill_combo_stock()

        self.assertEqual(stats["moved"], 0)
        self.assertEqual(stats["skipped_returned"], 1)
        self.assertEqual(self._qty(self.part_a), 10, "mã lẻ không được trừ")
        self.assertEqual(self._qty(self.part_b), 10)
        # Không báo động giả sau khi chạy: report dùng cùng công thức.
        self.assertEqual(combo_outstanding_report(), [])

    def test_report_doi_chieu_truoc_sau(self):
        self._legacy_sale(2)

        before = combo_outstanding_report()
        backfill_combo_stock()
        after = combo_outstanding_report()

        self.assertEqual(before, [{"product_id": self.combo.id, "outstanding": 2}])
        self.assertEqual(after, [])

    def test_management_command_chay_lai_duoc_sau_khi_khai_them_combo(self):
        """Migration 0038 chỉ chạy MỘT lần và chỉ xử lý combo đã khai lúc đó. Khai
        thêm combo về sau phải có đường chuyển sổ — chính là lệnh này."""
        self._legacy_sale()
        out = StringIO()

        call_command("backfill_combo_stock", stdout=out)

        self.assertEqual(self._qty(self.part_a), 9)
        self.assertEqual(self._qty(self.part_b), 8)
        self.assertEqual(self._qty(self.combo), 0)
        self.assertIn("Xong:", out.getvalue())

    def test_management_command_dry_run_khong_ghi_gi(self):
        self._legacy_sale()
        before = StockMovement.objects.count()
        out = StringIO()

        call_command("backfill_combo_stock", "--dry-run", stdout=out)

        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(self._qty(self.combo), -1)
        self.assertIn("DRY-RUN", out.getvalue())
