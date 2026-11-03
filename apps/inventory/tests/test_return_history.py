"""Lịch sử Nhập hoàn: mỗi dòng = 1 NHÓM ĐƠN HOÀN (ReturnEntry) đã ghi sổ."""
from __future__ import annotations

from datetime import date

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.return_history_service import (
    ReturnHistoryService,
)
from apps.inventory.models import ReturnType
from apps.inventory.tests.helpers import return_line as _line
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _seed():
    wh = Warehouse.objects.create(code="WH-RH", name="Kho A")
    channel = Channel.objects.create(code="lp-rh", name="RH")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000001", phone_normalized="0900000001"
    )
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    order = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000001",
        order_code="DH001", invoice_code="HD001", shipping_code="VD001",
        total_amount="1000",
    )
    posted = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=order.id,
        shipping_code="VD001",
        lines=[_line(p1, 4), _line(p2, 1)],
    )
    # Nhóm đơn nháp KHÔNG được tính.
    ReturnEntryService.create_entry(
        warehouse_id=wh.id, shipping_code="VD002", lines=[_line(p1, 9)]
    )
    return wh, order, posted, p1, p2


def test_list_page_only_posted_with_totals():
    wh, order, posted, p1, p2 = _seed()
    result = ReturnHistoryService.list_page(pagination={"limit": 50, "offset": 0})
    assert result["total"] == 1  # nhóm đơn nháp (SKU1 x9) bị loại
    assert result["total_quantity"] == 5  # SKU1 4 + SKU2 1 (không tính nháp)
    row = result["items"][0]
    assert row["return_id"] == posted.id  # id của NHÓM ĐƠN, không phải phiếu ngày
    assert row["code"] == posted.return_note.code
    assert row["code"].startswith("PTH-")
    assert row["source_order_id"] == order.id
    assert row["source_order_code"] == "DH001"
    assert row["source_order_invoice_code"] == "HD001"
    assert row["shipping_code"] == "VD001"
    assert row["warehouse_name"] == "Kho A"
    assert row["qty_total"] == 5
    assert row["return_type"] == ReturnType.DELIVERY_RETURNED
    assert row["return_type_label"] == "Hàng hoàn"


def test_list_page_row_type_is_mixed_when_lines_differ():
    wh, order, posted, p1, p2 = _seed()
    other = Warehouse.objects.create(code="WH-RH2", name="Kho B")
    ReturnEntryService.create_and_post_entry(
        warehouse_id=other.id,
        shipping_code="VD003",
        lines=[_line(p1, 1), _line(p2, 2, ReturnType.WARRANTY)],
    )
    rows = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0}, warehouse_ids=[other.id]
    )["items"]
    assert len(rows) == 1
    assert rows[0]["return_type"] == ""
    assert rows[0]["return_type_label"] == "Nhiều loại"


def test_list_page_product_filter_via_line():
    _seed()
    hit = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0}, product="SKU2"
    )
    assert hit["total"] == 1
    miss = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0}, product="SKU-NONE"
    )
    assert miss["total"] == 0


def test_list_page_filters_by_doc_date_not_created_at():
    """Lọc ngày theo doc_date (giờ VN) của phiếu ngày, không theo created_at (UTC)."""
    wh = Warehouse.objects.create(code="WH-RHD", name="Kho D")
    p = Product.objects.create(sku="SKU-D", name="SP D")
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        shipping_code="VDD1",
        doc_date=date(2026, 1, 5),
        lines=[_line(p, 3)],
    )
    inside = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0},
        warehouse_ids=[wh.id],
        date_from="2026-01-05",
        date_to="2026-01-05",
    )
    assert inside["total"] == 1
    assert inside["total_quantity"] == 3
    outside = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0},
        warehouse_ids=[wh.id],
        date_from="2026-01-06",
    )
    assert outside["total"] == 0


def test_list_page_filters_by_warehouse():
    wh, order, posted, p1, p2 = _seed()
    other = Warehouse.objects.create(code="WH-RH3", name="Kho C")
    assert (
        ReturnHistoryService.list_page(
            pagination={"limit": 50, "offset": 0}, warehouse_ids=[other.id]
        )["total"]
        == 0
    )
    assert (
        ReturnHistoryService.list_page(
            pagination={"limit": 50, "offset": 0}, warehouse_ids=[wh.id]
        )["total"]
        == 1
    )
