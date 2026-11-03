"""Nhóm vá robustness từ audit 2026-08-03.

- Payload trùng sản phẩm → lỗi nghiệp vụ 400, không IntegrityError 500.
- `StockDocumentLine.quantity` có chốt DB >= 1 (đồng bộ `ReturnNoteLine`).
- Phiếu kho `occurred_at` tương lai bị chặn lúc TẠO (trước đây kẹt lúc duyệt).
- Alert tồn âm chỉ bắn khi CHUYỂN sang âm (hết spam Lark mỗi lần bán tiếp).
- Purge scan 90 ngày giữ lại dòng còn nghi ngờ "cần phiếu bù".
- Manual match nhận cả scan CANCELLED (cron auto-pick nhầm đơn huỷ hết ngõ cụt).
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import (
    DocType,
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    StockDocument,
    StockDocumentLine,
)
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


# ── Trùng sản phẩm trong payload ──────────────────────────────────────────────


def test_return_entry_duplicate_lines_raise_business_error(warehouse, product):
    with pytest.raises(ValueError, match="trùng"):
        ReturnEntryService.create_entry(
            warehouse_id=warehouse.id,
            lines=[
                {"product_id": product.id, "quantity": 1, "return_type": "external_return"},
                {"product_id": product.id, "quantity": 2, "return_type": "external_return"},
            ],
            shipping_code="ROB-DUP-1",
        )


# ── CheckConstraint quantity >= 1 ─────────────────────────────────────────────


def test_stock_document_line_zero_quantity_blocked_at_db(warehouse, product):
    doc = StockDocumentService.create_document(
        doc_type=DocType.RECEIPT,
        dest_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
    )
    other = Product.objects.create(name="SP Rob 2", sku="ROB-2")
    with pytest.raises(IntegrityError), transaction.atomic():
        StockDocumentLine.objects.create(document=doc, product=other, quantity=0)


# ── occurred_at tương lai ─────────────────────────────────────────────────────


def test_create_document_rejects_future_occurred_at(warehouse, product):
    with pytest.raises(ValueError, match="tương lai"):
        StockDocumentService.create_document(
            doc_type=DocType.RECEIPT,
            dest_warehouse_id=warehouse.id,
            lines=[{"product_id": product.id, "quantity": 1}],
            occurred_at=timezone.now() + timedelta(days=1),
        )


# ── Alert tồn âm theo transition ──────────────────────────────────────────────


def test_negative_alert_fires_only_on_crossing(warehouse, product):
    r1 = StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-2,
        movement_type=MovementType.SALE,
    )
    assert r1.went_negative is True
    assert r1.crossed_negative is True

    # Đang âm bán tiếp: vẫn cảnh báo UI (went_negative) nhưng KHÔNG bắn alert mới.
    r2 = StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-1,
        movement_type=MovementType.SALE,
    )
    assert r2.went_negative is True
    assert r2.crossed_negative is False

    # Cộng thêm nhưng vẫn âm: không phải "chuyển sang âm".
    r3 = StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=1,
        movement_type=MovementType.RECEIPT,
    )
    assert r3.crossed_negative is False


def test_issue_for_order_publishes_alert_once_while_negative(warehouse, product):
    from apps.inventory.application.services import stock_sales_service
    from apps.inventory.application.services.stock_sales_service import (
        SaleLine,
        StockSalesService,
    )
    from apps.channels_core.models import Channel
    from apps.customers.models import Customer
    from apps.orders.models import Order

    ch = Channel.objects.create(code="rob-neg", name="Rob Neg")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000040", phone_normalized="0900000040"
    )

    def make_order(sfx):
        return Order.objects.create(
            channel=ch, customer=cust, name="K", phone="0900000040",
            order_code=f"DHR{sfx}", shipping_code=f"VDR{sfx}", total_amount="1",
            fulfillment_warehouse=warehouse,
        )

    with patch.object(stock_sales_service.event_bus, "publish_on_commit") as pub:
        warnings1 = StockSalesService.issue_for_order(
            order_id=make_order("1").id, warehouse_id=warehouse.id,
            lines=[SaleLine(product_id=product.id, quantity=2)],
        )
        warnings2 = StockSalesService.issue_for_order(
            order_id=make_order("2").id, warehouse_id=warehouse.id,
            lines=[SaleLine(product_id=product.id, quantity=1)],
        )

    # UI vẫn cảnh báo cả 2 lần; Lark chỉ nhận 1 alert (lần chuyển sang âm).
    assert len(warnings1) == 1 and len(warnings2) == 1
    # Order.objects.create() cũng publish OrderSaved (event bus 2026-09-23) — chỉ đếm alert kho.
    alert_calls = [c for c in pub.call_args_list if c.args[0].event_type != "orders.saved"]
    assert len(alert_calls) == 1


# ── Purge giữ scan còn nghi ngờ cần phiếu bù ─────────────────────────────────


def test_purge_keeps_scans_flagged_for_replacement(warehouse):
    from apps.inventory.tasks import purge_matched_pending_scans

    old = timezone.now() - timedelta(days=120)
    plain = PendingShipmentScan.objects.create(
        code="ROB-OLD-1", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    flagged = PendingShipmentScan.objects.create(
        code="ROB-OLD-2", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
        error="Đơn đã xuất kho trước đó (already_dispatched).",
        result_ref="",
    )
    PendingShipmentScan.objects.filter(
        id__in=[plain.id, flagged.id]
    ).update(created_at=old)

    purge_matched_pending_scans()

    assert not PendingShipmentScan.objects.filter(id=plain.id).exists()
    assert PendingShipmentScan.objects.filter(id=flagged.id).exists()


# ── Manual match cho scan CANCELLED ──────────────────────────────────────────


def test_manual_match_accepts_cancelled_scan(monkeypatch, warehouse):
    from apps.channels_core.models import Channel
    from apps.inventory.application.services.pending_scan_service import (
        PendingScanService,
    )
    from apps.inventory.tests.helpers import make_order as _make_order

    ch = Channel.objects.create(code="rob-cxl", name="Rob Cxl")
    order = _make_order(ch, warehouse, "r1", "ROB-CXL-1", qty=1)
    scan = PendingShipmentScan.objects.create(
        code="ROB-CXL-1", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.CANCELLED,
        error="Đơn về ở trạng thái huỷ",
    )

    def fake(scan_arg, order_arg, actor=None, *, warehouse_id=None):
        return f"order:{order_arg.id}", "đã xuất kho.", []

    monkeypatch.setattr(
        PendingScanService, "_complete_dispatch", staticmethod(fake)
    )

    result = PendingScanService.manual_match(scan_id=scan.id, order_id=order.id)

    assert result["ok"] is True
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.matched_order_id == order.id
