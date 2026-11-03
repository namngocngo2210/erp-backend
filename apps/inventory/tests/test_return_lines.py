"""Dòng hàng của nhóm đơn hoàn: loại hoàn per-line, điều chỉnh sau ghi sổ,
scan_batch_id xuyên suốt.

Gộp từ test_return_line_type / test_return_entry_adjust / test_return_scan_batch
(tái cấu trúc 2026-08-05).
"""
from __future__ import annotations

import uuid

import pytest
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.return_entry_service import (
    RETURN_ENTRY_REFERENCE,
    ReturnEntryService,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnEntry,
    ReturnStatus,
    ReturnType,
    StockItem,
    StockMovement,
)
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse


class ReturnLineTypeTests(TestCase):
    """Loại hoàn theo từng dòng nhóm đơn hoàn: ghi sổ per-line state, đánh dấu đơn
    RETURNED khi có dòng Hàng hoàn, huỷ đảo đúng từng state."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-LT", name="Kho LineType")
        self.p1 = Product.objects.create(name="SP1", sku="SKU-LT1")
        self.p2 = Product.objects.create(name="SP2", sku="SKU-LT2")
        self.p3 = Product.objects.create(name="SP3", sku="SKU-LT3")
        self.channel = Channel.objects.create(code="ch-lt", name="LT Channel")
        self.customer = Customer.objects.create(
            full_name="Khach LT",
            phone_raw="0912345670",
            phone_normalized="0912345670",
            code="CUST-LT",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach LT",
            phone="0912345670",
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _create(self, lines):
        return ReturnEntryService.create_entry(
            warehouse_id=self.warehouse.id,
            lines=lines,
            source_order_id=self.order.id,
        )

    def _qty(self, product, state):
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=product, state=state
        ).first()
        return item.quantity if item else 0

    def _states(self, entry_id):
        return dict(
            StockMovement.objects.filter(
                reference_type=RETURN_ENTRY_REFERENCE, reference_id=entry_id
            ).values_list("product_id", "state")
        )

    def test_line_return_type_persisted_via_create(self):
        entry = self._create(
            [{"product_id": self.p1.id, "quantity": 1, "return_type": "warranty"}]
        )
        self.assertEqual(entry.lines.get().return_type, "warranty")

    def test_post_mixed_line_types_records_per_line_state(self):
        entry = self._create(
            [
                {
                    "product_id": self.p1.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                },
                {"product_id": self.p2.id, "quantity": 1, "return_type": "warranty"},
                {"product_id": self.p3.id, "quantity": 1, "return_type": "defect"},
            ]
        )
        ReturnEntryService.post_entry(entry_id=entry.id)
        self.assertEqual(
            self._states(entry.id),
            {self.p1.id: "sellable", self.p2.id: "warranty", self.p3.id: "defect"},
        )

    def test_post_blocked_when_line_type_missing(self):
        with self.assertRaisesMessage(ValueError, "SKU-LT1"):
            self._create([{"product_id": self.p1.id, "quantity": 1}])

    def test_any_delivery_returned_line_marks_order_returned(self):
        entry = self._create(
            [
                {
                    "product_id": self.p1.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                },
                {"product_id": self.p2.id, "quantity": 1, "return_type": "warranty"},
            ]
        )
        ReturnEntryService.post_entry(entry_id=entry.id)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), int(OrderStatus.RETURNED))

    def test_no_delivery_returned_line_keeps_order_status(self):
        before = int(self.order.status)
        entry = self._create(
            [{"product_id": self.p1.id, "quantity": 1, "return_type": "warranty"}]
        )
        ReturnEntryService.post_entry(entry_id=entry.id)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), before)

    def test_cancel_mixed_reverses_each_state(self):
        entry = self._create(
            [
                {
                    "product_id": self.p1.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                },
                {"product_id": self.p2.id, "quantity": 1, "return_type": "warranty"},
            ]
        )
        ReturnEntryService.post_entry(entry_id=entry.id)
        self.assertEqual(self._qty(self.p1, "sellable"), 1)
        self.assertEqual(self._qty(self.p2, "warranty"), 1)

        ReturnEntryService.cancel_entry(entry_id=entry.id)
        self.assertEqual(self._qty(self.p1, "sellable"), 0)
        self.assertEqual(self._qty(self.p2, "warranty"), 0)


# ---------------------------------------------------------------------------
# Điều chỉnh số lượng dòng hàng của nhóm đơn hoàn ĐÃ GHI SỔ (adjust_entry).
#
# Ca gốc: đơn 2 SP nhưng chỉ 1 SP về kho, phiếu hoàn đã ghi sổ full đơn → tồn ảo.
# Kế toán/kho điều chỉnh tại Lịch sử nhập xuất thay vì Huỷ + quét lại.
# ---------------------------------------------------------------------------


def _lines(*pairs):
    return [
        {
            "product_id": product.id,
            "quantity": qty,
            "return_type": ReturnType.DELIVERY_RETURNED,
        }
        for product, qty in pairs
    ]


def _sellable(wh, product):
    item = StockItem.objects.filter(
        warehouse=wh, product=product, state="sellable"
    ).first()
    return item.quantity if item else 0


@pytest.mark.django_db
def test_adjust_removes_line_and_reverses_stock(warehouse_with_two_products):
    """Ca gốc: hoàn full 2 SP nhưng thực nhận 1 → hạ SP kia về 0."""
    wh, p1, p2 = warehouse_with_two_products
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        lines=_lines((p1, 1), (p2, 1)),
        shipping_code="ADJ-SPX1",
    )
    assert _sellable(wh, p1) == 1
    assert _sellable(wh, p2) == 1

    adjusted = ReturnEntryService.adjust_entry(
        entry_id=entry.id, lines=[{"product_id": p2.id, "quantity": 0}]
    )

    assert adjusted.status == ReturnStatus.POSTED
    assert _sellable(wh, p1) == 1
    assert _sellable(wh, p2) == 0
    # Dòng SP2 bị gỡ khỏi phiếu; SP1 giữ nguyên.
    remaining = {line.product_id: line.quantity for line in adjusted.lines.all()}
    assert remaining == {p1.id: 1}


@pytest.mark.django_db
def test_adjust_lowers_quantity_writes_delta_movement(warehouse_with_two_products):
    wh, p1, _ = warehouse_with_two_products
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines((p1, 3)), shipping_code="ADJ-SPX2"
    )
    assert _sellable(wh, p1) == 3

    ReturnEntryService.adjust_entry(
        entry_id=entry.id, lines=[{"product_id": p1.id, "quantity": 1}]
    )

    assert _sellable(wh, p1) == 1
    # Bút toán điều chỉnh là movement RETURN_IN delta âm trên cùng reference —
    # sổ movement tự cân (3 - 2 = 1), không sửa đè movement cũ.
    deltas = list(
        StockMovement.objects.filter(
            reference_type="return_entry", reference_id=entry.id, product_id=p1.id
        ).values_list("qty_delta", flat=True)
    )
    assert sorted(deltas) == [-2, 3]


@pytest.mark.django_db
def test_adjust_raise_within_outstanding_allowed(warehouse_with_two_products):
    """Nhận thêm hàng sau khi đã ghi sổ thiếu — tăng trong phần đơn còn outstanding."""
    wh, p1, _ = warehouse_with_two_products
    channel = Channel.objects.create(code="ch-adj", name="Kenh ADJ")
    customer = Customer.objects.create(
        full_name="Khach ADJ",
        phone_raw="0900000001",
        phone_normalized="0900000001",
        code="CUST-ADJ",
    )
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach ADJ",
        phone="0900000001",
        total_amount="100000",
        status=int(OrderStatus.DELIVERED_SUCCESS),
        fulfillment_warehouse=wh,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id,
        product_id=p1.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    StockSalesService.issue_for_order(
        order_id=order.id,
        warehouse_id=wh.id,
        lines=[SaleLine(product_id=p1.id, quantity=2)],
    )
    assert _sellable(wh, p1) == 8

    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        lines=_lines((p1, 1)),
        shipping_code="ADJ-SPX3",
        source_order_id=order.id,
    )
    assert _sellable(wh, p1) == 9

    ReturnEntryService.adjust_entry(
        entry_id=entry.id, lines=[{"product_id": p1.id, "quantity": 2}]
    )
    assert _sellable(wh, p1) == 10

    # Tăng vượt phần đã trừ tồn của đơn nguồn → guard chặn, tồn không đổi.
    with pytest.raises(ValueError, match="chỉ còn"):
        ReturnEntryService.adjust_entry(
            entry_id=entry.id, lines=[{"product_id": p1.id, "quantity": 3}]
        )
    assert _sellable(wh, p1) == 10


@pytest.mark.django_db
def test_adjust_all_zero_rejected_use_cancel_instead(warehouse_with_two_products):
    wh, p1, p2 = warehouse_with_two_products
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        lines=_lines((p1, 1), (p2, 1)),
        shipping_code="ADJ-SPX4",
    )
    with pytest.raises(ValueError, match="Huỷ"):
        ReturnEntryService.adjust_entry(
            entry_id=entry.id,
            lines=[
                {"product_id": p1.id, "quantity": 0},
                {"product_id": p2.id, "quantity": 0},
            ],
        )
    assert _sellable(wh, p1) == 1
    assert _sellable(wh, p2) == 1


@pytest.mark.django_db
def test_adjust_unknown_product_rejected(warehouse_with_two_products):
    wh, p1, p2 = warehouse_with_two_products
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines((p1, 1)), shipping_code="ADJ-SPX5"
    )
    with pytest.raises(ValueError, match="không có trong phiếu"):
        ReturnEntryService.adjust_entry(
            entry_id=entry.id, lines=[{"product_id": p2.id, "quantity": 1}]
        )


@pytest.mark.django_db
def test_adjust_non_posted_rejected(warehouse_with_two_products):
    wh, p1, _ = warehouse_with_two_products
    entry = ReturnEntryService.create_entry(
        warehouse_id=wh.id, lines=_lines((p1, 1)), shipping_code="ADJ-SPX6"
    )
    with pytest.raises(ValueError, match="ghi sổ"):
        ReturnEntryService.adjust_entry(
            entry_id=entry.id, lines=[{"product_id": p1.id, "quantity": 0}]
        )


class AdjustReturnApiTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_adj")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WADJ", name="Kho ADJ API")
        self.p1 = Product.objects.create(name="SP A1", sku="ADJA-1")
        self.p2 = Product.objects.create(name="SP A2", sku="ADJA-2")
        self.entry = ReturnEntryService.create_and_post_entry(
            warehouse_id=self.wh.id,
            lines=[
                {
                    "product_id": self.p1.id,
                    "quantity": 1,
                    "return_type": ReturnType.DELIVERY_RETURNED,
                },
                {
                    "product_id": self.p2.id,
                    "quantity": 1,
                    "return_type": ReturnType.DELIVERY_RETURNED,
                },
            ],
            shipping_code="ADJ-API-1",
        )

    def test_adjust_endpoint_updates_lines(self):
        resp = self.client.post(
            f"/api/inventory/returns/{self.entry.id}/adjust",
            data={"lines": [{"product_id": self.p2.id, "quantity": 0}]},
            format="json",
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["status"] == "posted"
        assert [(line["product"], line["quantity"]) for line in body["lines"]] == [
            (self.p1.id, 1)
        ]

    def test_adjust_endpoint_all_zero_returns_400(self):
        resp = self.client.post(
            f"/api/inventory/returns/{self.entry.id}/adjust",
            data={
                "lines": [
                    {"product_id": self.p1.id, "quantity": 0},
                    {"product_id": self.p2.id, "quantity": 0},
                ]
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_adjust_endpoint_missing_entry_returns_404(self):
        resp = self.client.post(
            "/api/inventory/returns/999999/adjust",
            data={"lines": [{"product_id": self.p1.id, "quantity": 0}]},
            format="json",
        )
        assert resp.status_code == 404


class ScanBatchIdPropagationTests(TestCase):
    """Đợt quét trả hàng: scan_batch_id xuyên suốt tạo nhóm đơn hoàn / pending scan /
    ledger (xem docs/erp-scan-batch-grouping.md)."""

    def setUp(self):
        self.user = make_admin("admin_scan_batch")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WSB1", name="Kho Đợt Quét")
        self.product = Product.objects.create(name="SP Đợt", sku="BATCH-1")

    def _lines(self, qty=1):
        return [
            {
                "product_id": self.product.id,
                "quantity": qty,
                "return_type": "warranty",
            }
        ]

    def test_create_entry_persists_scan_batch_id(self):
        batch = uuid.uuid4()
        entry = ReturnEntryService.create_entry(
            warehouse_id=self.wh.id,
            lines=self._lines(),
            scan_batch_id=batch,
        )
        entry.refresh_from_db()
        assert entry.scan_batch_id == batch

    def test_api_create_returns_scan_batch_id(self):
        batch = str(uuid.uuid4())
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "scan_batch_id": batch,
                "lines": self._lines(qty=2),
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        assert resp.json()["scan_batch_id"] == batch

    # NOTE: sổ kho (`ledger-documents`) không còn phơi scan_batch_id — dòng phiếu
    # trả giờ là PHIẾU NGÀY, còn đợt quét là thuộc tính của từng nhóm đơn bên
    # trong. Việc gom nhóm theo đợt quét được kiểm ở API nhóm đơn
    # (test_api_create_returns_scan_batch_id) và ở luồng khớp pending bên dưới.

    def test_pending_scan_match_inherits_scan_batch_id(self):
        batch = uuid.uuid4()
        channel = Channel.objects.create(code="lp-scan-batch", name="Scan Batch Channel")
        customer = Customer.objects.create(
            full_name="Khach Batch",
            phone_raw="0929000099",
            phone_normalized="0929000099",
            code="CUST-SB-1",
        )
        order = Order.objects.create(
            channel=channel,
            customer=customer,
            name="Khach Batch",
            phone="0929000099",
            total_amount="100000",
            status=OrderStatus.AWAITING_SHIPMENT,
            shipping_code="VNSB001",
            fulfillment_warehouse=self.wh,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=1,
            unit_price=100000,
            line_total=100000,
        )
        results = PendingScanService.create_batch(
            kind=PendingScanKind.RETURN,
            warehouse_id=self.wh.id,
            codes=["VNSB001"],
            return_type="delivery_returned",
            user_id=self.user.id,
            scan_batch_id=batch,
        )
        assert results == [{"code": "VNSB001", "ok": True}]
        scan = PendingShipmentScan.objects.get(code="VNSB001")
        assert scan.scan_batch_id == batch

        summary = PendingScanService.match_pending_scans()
        assert summary["matched"] == 1
        scan.refresh_from_db()
        assert scan.status == PendingScanStatus.MATCHED
        entry = ReturnEntry.objects.get(shipping_code="VNSB001")
        assert entry.scan_batch_id == batch
        assert entry.source_order_id == order.id
