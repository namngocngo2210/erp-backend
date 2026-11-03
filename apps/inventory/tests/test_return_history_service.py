"""Test gộp theo SKU của ReturnHistoryService (view Theo SKU tab Nhập hoàn)."""
from __future__ import annotations

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.return_history_service import (
    ReturnHistoryService,
)
from apps.inventory.tests.helpers import return_line as _line
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.users.models import User
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _seed_returns():
    ch = Channel.objects.create(code="lp-rsku", name="RSKU")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000008", phone_normalized="0900000008"
    )
    wh = Warehouse.objects.create(code="WH-RSKU", name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    o1 = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000008",
        order_code="DH001", invoice_code="HD001", total_amount="1000",
    )
    # SKU1 hoàn ở 2 nhóm đơn (2 + 3) → qty_total 5, return_count 2; SKU2 hoàn 1.
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=o1.id,
        shipping_code="VD-RSKU-1",
        lines=[_line(p1, 2), _line(p2, 1)],
    )
    e2 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=o1.id,
        shipping_code="VD-RSKU-2",
        lines=[_line(p1, 3)],
    )
    # Nhóm đơn nháp KHÔNG được tính.
    ReturnEntryService.create_entry(
        warehouse_id=wh.id, shipping_code="VD-RSKU-D", lines=[_line(p1, 9)]
    )
    return wh, o1, e1, e2, p1, p2


def test_return_skus_page_groups_by_sku():
    _seed_returns()
    data = ReturnHistoryService.skus_page(pagination={"limit": 50, "offset": 0})
    assert data["total"] == 2
    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["SKU1"]["return_count"] == 2
    assert by_sku["SKU1"]["qty_total"] == 5  # nháp không tính
    assert by_sku["SKU2"]["qty_total"] == 1
    assert data["total_quantity"] == 6


def test_return_sku_returns_drilldown():
    wh, o1, e1, e2, p1, p2 = _seed_returns()
    rows = ReturnHistoryService.sku_returns(product_id=p1.id)
    by_id = {r["return_id"]: r for r in rows}
    assert set(by_id) == {e1.id, e2.id}
    assert by_id[e1.id]["quantity"] == 2
    assert by_id[e2.id]["quantity"] == 3
    assert by_id[e1.id]["source_order_invoice_code"] == "HD001"
    assert by_id[e1.id]["code"] == e1.return_note.code
    assert by_id[e1.id]["code"].startswith("PTH-")
    assert by_id[e1.id]["warehouse_name"] == "Kho A"
    assert by_id[e1.id]["return_type_label"] == "Hàng hoàn"
    assert by_id[e2.id]["shipping_code"] == "VD-RSKU-2"


def test_list_page_includes_sku_lines():
    _wh, _o1, e1, e2, _p1, _p2 = _seed_returns()
    data = ReturnHistoryService.list_page(pagination={"limit": 50, "offset": 0})
    # e1 + e2 cùng ngày/kho → CÙNG mã phiếu ngày, nên key theo nhóm đơn (return_id).
    by_id = {i["return_id"]: i for i in data["items"]}

    # Nhóm đơn 1: 2 SKU, sắp xếp SL giảm dần
    assert [(ln["sku"], ln["qty"]) for ln in by_id[e1.id]["lines"]] == [
        ("SKU1", 2),
        ("SKU2", 1),
    ]
    assert by_id[e1.id]["qty_total"] == 3
    # Nhóm đơn 2: 1 SKU
    assert [(ln["sku"], ln["qty"]) for ln in by_id[e2.id]["lines"]] == [("SKU1", 3)]
    # Nhóm đơn nháp không xuất hiện
    assert set(by_id) == {e1.id, e2.id}


def test_list_page_performed_by_is_entry_creator():
    wh, o1, e1, e2, p1, _p2 = _seed_returns()  # e1/e2 tạo không có user
    scanner = User.objects.create_user(
        username="scanner-ret", password="x", display_name="Nam Kho"
    )
    e3 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=o1.id,
        shipping_code="VD-RSKU-3",
        lines=[_line(p1, 1)],
        user_id=scanner.id,
    )
    data = ReturnHistoryService.list_page(pagination={"limit": 50, "offset": 0})
    by_id = {i["return_id"]: i for i in data["items"]}
    assert by_id[e3.id]["performed_by"] == "Nam Kho"
    # Nhóm đơn cũ không có user → chuỗi rỗng, không lỗi.
    assert by_id[e1.id]["performed_by"] == ""
    assert by_id[e2.id]["performed_by"] == ""


def test_list_page_lines_are_entry_level_not_product_filtered():
    # Bộ lọc sản phẩm ở tab Nhập hoàn lọc NHÓM ĐƠN (Exists), không lọc dòng — nên
    # `lines` phải hiện đủ mọi SKU của nhóm khớp, giống hệt drill-down `lines()`.
    _wh, _o1, e1, _e2, _p1, _p2 = _seed_returns()
    data = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0}, product="SKU2"
    )
    by_id = {i["return_id"]: i for i in data["items"]}

    assert set(by_id) == {e1.id}
    assert {(ln["sku"], ln["qty"]) for ln in by_id[e1.id]["lines"]} == {
        ("SKU1", 2),
        ("SKU2", 1),
    }
    assert by_id[e1.id]["qty_total"] == 3
