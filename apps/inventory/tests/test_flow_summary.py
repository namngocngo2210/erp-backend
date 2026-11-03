"""Số tổng hợp đầu trang Lịch sử nhập xuất + phân loại đơn xuất theo ĐVVC."""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.flow_summary_service import (
    FlowSummaryService,
    resolve_carrier,
)
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import DocType, MovementType, ReturnType
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _make_order(ch, cust, *, code, provider="", shipping_code=""):
    return Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000030",
        order_code=code, invoice_code=f"HD-{code}", shipping_code=shipping_code,
        shipping_provider=provider, total_amount="1000",
    )


def _seed():
    ch = Channel.objects.create(code="lp-sum", name="SUM")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000030", phone_normalized="0900000030"
    )
    wh = Warehouse.objects.create(code="WH-SUM", name="Kho S")
    wh2 = Warehouse.objects.create(code="WH-SUM2", name="Kho S2")
    p1 = Product.objects.create(sku="SM01", name="SP sum")
    return ch, cust, wh, wh2, p1


def _dispatch(order, wh, product, qty):
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=product.id, qty_delta=-qty,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )


def test_resolve_carrier_provider_first_then_code_prefix():
    # provider_code KShip thắng, bất kể mã vận đơn.
    assert resolve_carrier("ghn", "S123.A1") == "GHN"
    assert resolve_carrier("GiaoHangTietKiem", "") == "GHTK"
    # KShip trả code thô "J&T" (có "&"), không phải "JT" — map phải nhận đúng chuỗi đó.
    assert resolve_carrier("J&T", "") == "J&T Express"
    assert resolve_carrier("SPX", "") == "SPX Express"
    # provider lạ (KShip thêm đối tác mới chưa vào map) → "Khác", chờ bổ sung luật.
    assert resolve_carrier("ahamove", "") == "Khác"
    # provider trống → nhận diện theo đầu mã (chỉ pattern chắc chắn).
    assert resolve_carrier("", "SPXVN012345") == "SPX Express"
    assert resolve_carrier("", "S1234567.A1.B2") == "GHTK"
    assert resolve_carrier("", "7712345678901") == "Best Express"
    assert resolve_carrier("", "JT0011223344") == "J&T Express"
    # Không nhận diện được → "Khác".
    assert resolve_carrier("", "ZZ999") == "Khác"
    assert resolve_carrier("", "") == "Khác"


def test_summary_totals_and_carriers():
    ch, cust, wh, wh2, p1 = _seed()
    o1 = _make_order(ch, cust, code="D1", provider="ghn", shipping_code="G1")
    o2 = _make_order(ch, cust, code="D2", provider="", shipping_code="S99.A.B")
    o3 = _make_order(ch, cust, code="D3", provider="", shipping_code="XYZ")
    _dispatch(o1, wh, p1, 2)
    _dispatch(o2, wh, p1, 3)
    _dispatch(o3, wh, p1, 1)

    # Phiếu xuất kho 4 + phiếu chuyển kho 5 (đều đã duyệt).
    xk = StockDocumentService.create_document(
        doc_type=DocType.ISSUE, source_warehouse_id=wh.id,
        lines=[{"product_id": p1.id, "quantity": 4}],
    )
    StockDocumentService.submit_for_approval(document_id=xk.id)
    StockDocumentService.approve_document(document_id=xk.id)
    ck = StockDocumentService.create_document(
        doc_type=DocType.TRANSFER, source_warehouse_id=wh.id,
        dest_warehouse_id=wh2.id, lines=[{"product_id": p1.id, "quantity": 5}],
    )
    StockDocumentService.submit_for_approval(document_id=ck.id)
    StockDocumentService.approve_document(document_id=ck.id)

    # 1 nhóm đơn hoàn đã ghi sổ (hoàn 2 của o1).
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, source_order_id=o1.id,
        lines=[{"product_id": p1.id, "quantity": 2, "return_type": ReturnType.DELIVERY_RETURNED}],
    )

    data = FlowSummaryService.summary()
    assert data["issue"] == {
        "total_quantity": 6 + 4 + 5,
        "sale_quantity": 6,
        "doc_quantity": 4,
        "transfer_quantity": 5,
        "order_count": 3,
    }
    assert data["return"] == {
        "total_quantity": 2 + 5,
        "return_quantity": 2,
        "transfer_quantity": 5,
        "return_count": 1,
    }
    by_label = {c["label"]: c for c in data["carriers"]}
    assert by_label["GHN"] == {"label": "GHN", "order_count": 1, "quantity": 2}
    assert by_label["GHTK"] == {"label": "GHTK", "order_count": 1, "quantity": 3}
    assert by_label["Khác"] == {"label": "Khác", "order_count": 1, "quantity": 1}
    # "Khác" luôn cuối danh sách.
    assert data["carriers"][-1]["label"] == "Khác"


def test_summary_warehouse_filter_scopes_carriers():
    ch, cust, wh, wh2, p1 = _seed()
    o1 = _make_order(ch, cust, code="D1", provider="ghn", shipping_code="G1")
    o2 = _make_order(ch, cust, code="D2", provider="best", shipping_code="B1")
    _dispatch(o1, wh, p1, 2)
    _dispatch(o2, wh2, p1, 3)

    data = FlowSummaryService.summary(warehouse_ids=[wh.id])
    assert data["issue"]["sale_quantity"] == 2
    assert [c["label"] for c in data["carriers"]] == ["GHN"]


def test_summary_excludes_fully_reversed_orders():
    ch, cust, wh, _wh2, p1 = _seed()
    o1 = _make_order(ch, cust, code="D1", provider="ghn", shipping_code="G1")
    _dispatch(o1, wh, p1, 2)
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=2,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order",
        reference_id=o1.id,
    )
    data = FlowSummaryService.summary()
    assert data["issue"]["sale_quantity"] == 0
    assert data["issue"]["order_count"] == 0
    assert data["carriers"] == []


def test_carrier_and_product_filters():
    """Lọc ĐVVC + lọc nhiều sản phẩm xuyên suốt summary và 2 tab."""
    from apps.inventory.application.services.issue_document_history import (
        MergedIssueHistory,
    )
    from apps.inventory.application.services.transfer_document_history import (
        MergedReturnHistory,
    )

    ch, cust, wh, wh2, p1 = _seed()
    p2 = Product.objects.create(sku="SM02", name="SP sum 2")
    o_ghn = _make_order(ch, cust, code="D1", provider="ghn", shipping_code="G1")
    o_ghtk = _make_order(ch, cust, code="D2", provider="", shipping_code="S99.A.B")
    _dispatch(o_ghn, wh, p1, 2)
    _dispatch(o_ghtk, wh, p2, 3)

    # Phiếu chuyển 5 — không có ĐVVC nên phải biến mất khi lọc ĐVVC.
    ck = StockDocumentService.create_document(
        doc_type=DocType.TRANSFER, source_warehouse_id=wh.id,
        dest_warehouse_id=wh2.id, lines=[{"product_id": p1.id, "quantity": 5}],
    )
    StockDocumentService.submit_for_approval(document_id=ck.id)
    StockDocumentService.approve_document(document_id=ck.id)

    # Hoàn 1 của đơn GHN.
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, source_order_id=o_ghn.id,
        lines=[{"product_id": p1.id, "quantity": 1, "return_type": ReturnType.DELIVERY_RETURNED}],
    )

    # Lọc ĐVVC = GHN: chỉ còn đơn GHN, phiếu kho/chuyển kho về 0.
    data = FlowSummaryService.summary(carrier="GHN")
    assert data["issue"] == {
        "total_quantity": 2, "sale_quantity": 2, "doc_quantity": 0,
        "transfer_quantity": 0, "order_count": 1,
    }
    assert [c["label"] for c in data["carriers"]] == ["GHN"]
    assert data["return"]["return_quantity"] == 1
    assert data["return"]["transfer_quantity"] == 0

    issue_page = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, carrier="GHN"
    )
    assert [i["order_id"] for i in issue_page["items"]] == [o_ghn.id]
    ret_page = MergedReturnHistory.list_page(
        pagination={"limit": 50, "offset": 0}, carrier="GHTK"
    )
    assert ret_page["items"] == []  # nhóm hoàn thuộc đơn GHN, lọc GHTK phải rỗng

    # Lọc "Khác": không đơn nào (cả 2 đơn đều nhận diện được ĐVVC).
    data_other = FlowSummaryService.summary(carrier="Khác")
    assert data_other["issue"]["order_count"] == 0

    # Lọc nhiều sản phẩm: chỉ SM02.
    data_pid = FlowSummaryService.summary(product_ids=[p2.id])
    assert data_pid["issue"]["sale_quantity"] == 3
    assert data_pid["issue"]["transfer_quantity"] == 0  # phiếu chuyển chỉ chứa SM01
    assert [c["label"] for c in data_pid["carriers"]] == ["GHTK"]


def test_summary_endpoint():
    ch, cust, wh, _wh2, p1 = _seed()
    o1 = _make_order(ch, cust, code="D1", provider="ghn", shipping_code="G1")
    _dispatch(o1, wh, p1, 2)

    client = APIClient()
    client.force_authenticate(make_admin("admin-flowsum"))
    r = client.get("/api/inventory/dispatch-history/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["issue"]["total_quantity"] == 2
    assert body["return"]["total_quantity"] == 0
    assert body["carriers"] == [{"label": "GHN", "order_count": 1, "quantity": 2}]

    bad = client.get("/api/inventory/dispatch-history/summary?date_from=xx")
    assert bad.status_code == 400

    # Lọc qua query param carrier + product_ids.
    ok = client.get(
        "/api/inventory/dispatch-history/summary?carrier=GHTK&product_ids=1,abc"
    )
    assert ok.status_code == 200
    assert ok.json()["issue"]["sale_quantity"] == 0
