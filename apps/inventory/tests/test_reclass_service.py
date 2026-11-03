"""Tests cho hardening reclass: check tồn khả dụng, log truy vết, RBAC disposal."""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_reclass_service import StockReclassService
from apps.inventory.models import (
    MovementType,
    StockItem,
    StockMovement,
    StockReclassLog,
    StockState,
)
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin, make_user_with_sections
from apps.warehouses.models import Warehouse


class StockReclassServiceTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-RC", name="Kho Reclass")
        self.product = Product.objects.create(name="SP Reclass", sku="SKU-RC")
        # 3 đơn vị tồn warranty
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=3,
            movement_type=MovementType.RETURN_IN,
            state=StockState.WARRANTY,
        )

    def _qty(self, state):
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state=state
        ).first()
        return item.quantity if item else 0

    def test_reclass_beyond_available_raises(self):
        with self.assertRaises(ValueError):
            StockReclassService.reclassify(
                warehouse_id=self.warehouse.id,
                product_id=self.product.id,
                quantity=5,
                from_state=StockState.WARRANTY,
                action="scrap",
            )
        self.assertEqual(self._qty(StockState.WARRANTY), 3, "Tồn không được thay đổi")

    def test_reclass_on_empty_state_raises(self):
        with self.assertRaises(ValueError):
            StockReclassService.reclassify(
                warehouse_id=self.warehouse.id,
                product_id=self.product.id,
                quantity=1,
                from_state=StockState.DEFECT,
                action="scrap",
            )

    def test_scrap_writes_log_and_references_it(self):
        log = StockReclassService.reclassify(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            quantity=2,
            from_state=StockState.WARRANTY,
            action="scrap",
        )
        self.assertEqual(self._qty(StockState.WARRANTY), 1)
        self.assertIsInstance(log, StockReclassLog)
        mv = StockMovement.objects.get(
            reference_type="reclass",
            reference_id=log.id,
            movement_type=MovementType.DISPOSAL,
        )
        self.assertEqual(mv.qty_delta, -2)

    def test_to_sellable_moves_between_states(self):
        StockReclassService.reclassify(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            quantity=3,
            from_state=StockState.WARRANTY,
            action="to_sellable",
        )
        self.assertEqual(self._qty(StockState.WARRANTY), 0)
        self.assertEqual(self._qty(StockState.SELLABLE), 3)


class ReclassRbacTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-RC2", name="Kho Reclass 2")
        self.product = Product.objects.create(name="SP Reclass 2", sku="SKU-RC2")
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=5,
            movement_type=MovementType.RETURN_IN,
            state=StockState.DEFECT,
        )
        self.client = APIClient()

    def _reclass(self, action):
        return self.client.post(
            "/api/inventory/stock/reclassify",
            data={
                "warehouse_id": self.warehouse.id,
                "product_id": self.product.id,
                "quantity": 1,
                "from_state": "defect",
                "action": action,
            },
            format="json",
        )

    def test_disposal_requires_approve_permission(self):
        """Nhân viên kho (section stock, không có stock-documents:approve)
        không được xuất huỷ — bịt đường bypass cổng duyệt kế toán."""
        staff = make_user_with_sections("wh-staff-rc", ["stock"])
        self.client.force_authenticate(staff)

        self.assertEqual(self._reclass("scrap").status_code, 403)
        self.assertEqual(self._reclass("send_back").status_code, 403)
        # to_sellable không xuất hàng khỏi tồn → vẫn được phép
        self.assertEqual(self._reclass("to_sellable").status_code, 200)

    def test_disposal_allowed_with_approve_permission(self):
        admin = make_admin("admin-rc")
        self.client.force_authenticate(admin)
        self.assertEqual(self._reclass("scrap").status_code, 200)
