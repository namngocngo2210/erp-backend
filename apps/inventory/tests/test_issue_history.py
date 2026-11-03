"""Lịch sử tab Xuất: danh sách đơn xuất, gộp phiếu Xuất kho, tổng/tile khớp bảng."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.flow_summary_service import FlowSummaryService
from apps.inventory.application.services.issue_document_history import (
    IssueDocumentHistoryService,
    MergedIssueHistory,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.application.services.stock_sales_service import (
    CANCEL_RELEASE_NOTE,
    COMBO_BACKFILL_ISSUE_NOTE,
    COMBO_BACKFILL_RELEASE_NOTE,
)
from apps.inventory.models import DocType, MovementType, StockMovement
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.users.models import User
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Danh sách đơn xuất (issue orders), gộp theo order với net = dispatched -
# reversed. (gốc: test_issue_orders_history.py)
# ---------------------------------------------------------------------------


def _seed_orders():
    channel = Channel.objects.create(code="lp-ioh", name="IOH")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000099", phone_normalized="0900000099"
    )
    wh = Warehouse.objects.create(code="WH-IOH", name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    order = Order.objects.create(
        channel=channel, customer=customer, order_code="DH001",
        invoice_code="HD001", shipping_code="VD001",
    )
    ref = {"reference_type": "order", "reference_id": order.id}
    # Đơn xuất 2 SKU, hoàn 1 phần SKU1.
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.SALE, **ref,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p2.id, qty_delta=-3,
        movement_type=MovementType.SALE, **ref,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=2,
        movement_type=MovementType.SALE_REVERSAL, **ref,
    )
    return wh, order, p1, p2


def test_orders_page_groups_by_order_with_net():
    wh, order, p1, p2 = _seed_orders()
    result = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}
    )
    assert result["total"] == 1
    row = result["items"][0]
    assert row["order_id"] == order.id
    assert row["order_code"] == "DH001"
    assert row["invoice_code"] == "HD001"
    assert row["shipping_code"] == "VD001"
    assert row["warehouse_name"] == "Kho A"
    assert row["sku_count"] == 2
    assert row["dispatched"] == 8        # 5 + 3
    assert row["reversed"] == 2
    assert row["net"] == 6               # 8 - 2
    assert row["last_at"] is not None
    assert result["total_quantity"] == 6  # tổng net toàn bộ đơn khớp lọc


def test_orders_page_order_filter():
    wh, order, p1, p2 = _seed_orders()
    empty = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, order="NOPE"
    )
    assert empty["total"] == 0
    hit = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, order="VD001"
    )
    assert hit["total"] == 1


def test_orders_page_warehouse_name_is_latest_movement():
    channel = Channel.objects.create(code="lp-ioh2", name="IOH2")
    customer = Customer.objects.create(
        full_name="K2", phone_raw="0900000098", phone_normalized="0900000098"
    )
    wh_a = Warehouse.objects.create(code="WH-A", name="Kho A")
    wh_b = Warehouse.objects.create(code="WH-B", name="Kho B")
    p1 = Product.objects.create(sku="SKU-WA1", name="Áo A")
    p2 = Product.objects.create(sku="SKU-WA2", name="Áo B")
    order = Order.objects.create(
        channel=channel, customer=customer, order_code="DH100", shipping_code="VD100"
    )
    ref = {"reference_type": "order", "reference_id": order.id}

    r1 = StockCommandService.record_movement(
        warehouse_id=wh_a.id, product_id=p1.id, qty_delta=-1,
        movement_type=MovementType.SALE, **ref,
    )
    r2 = StockCommandService.record_movement(
        warehouse_id=wh_b.id, product_id=p2.id, qty_delta=-1,
        movement_type=MovementType.SALE, **ref,
    )
    # Ép cứng created_at để đảm bảo thứ tự bất kể tốc độ chạy test.
    now = timezone.now()
    StockMovement.objects.filter(id=r1.movement_id).update(
        created_at=now - timedelta(minutes=1)
    )
    StockMovement.objects.filter(id=r2.movement_id).update(created_at=now)

    result = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    assert result["total"] == 1
    row = result["items"][0]
    assert row["order_id"] == order.id
    assert row["warehouse_name"] == "Kho B"


def test_orders_page_paginates_by_order_count():
    channel = Channel.objects.create(code="lp-ioh3", name="IOH3")
    customer = Customer.objects.create(
        full_name="K3", phone_raw="0900000097", phone_normalized="0900000097"
    )
    wh = Warehouse.objects.create(code="WH-PG", name="Kho PG")
    p1 = Product.objects.create(sku="SKU-PG1", name="Áo PG")
    order1 = Order.objects.create(
        channel=channel, customer=customer, order_code="DH200", shipping_code="VD200"
    )
    order2 = Order.objects.create(
        channel=channel, customer=customer, order_code="DH201", shipping_code="VD201"
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-1,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order1.id,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-1,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order2.id,
    )

    page1 = DispatchHistoryService.orders_page(pagination={"limit": 1, "offset": 0})
    assert page1["total"] == 2
    assert len(page1["items"]) == 1

    page2 = DispatchHistoryService.orders_page(pagination={"limit": 1, "offset": 1})
    assert page2["total"] == 2
    assert len(page2["items"]) == 1

    seen_order_ids = {page1["items"][0]["order_id"], page2["items"][0]["order_id"]}
    assert seen_order_ids == {order1.id, order2.id}


# ---------------------------------------------------------------------------
# Gộp Phiếu Xuất kho (StockDocument issue) vào báo cáo tab Xuất.
#
# Bao gồm IssueDocumentHistoryService (nguồn phiếu kho) và MergedIssueHistory
# (trộn xuất-theo-đơn + phiếu kho, có breakdown).
# (gốc: test_issue_document_history.py)
# ---------------------------------------------------------------------------


def _post_issue_doc(wh, product, qty, *, user=None, note=""):
    """Tạo + ghi sổ 1 Phiếu Xuất kho (issue) → trừ tồn qua movement issue/document."""
    uid = user.id if user else None
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=wh.id,
        lines=[{"product_id": product.id, "quantity": qty}],
        note=note,
        user_id=uid,
    )
    StockDocumentService.submit_for_approval(document_id=doc.id, user_id=uid)
    StockDocumentService.approve_document(document_id=doc.id, user_id=uid)
    return doc


def _seed_docs():
    ch = Channel.objects.create(code="lp-doc", name="DOC")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000010", phone_normalized="0900000010"
    )
    wh = Warehouse.objects.create(code="WH-DOC", name="Kho D")
    p1 = Product.objects.create(sku="DV20", name="Đầu ghi")
    p2 = Product.objects.create(sku="DV21", name="Camera")
    order = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000010",
        order_code="DHX1", invoice_code="HDX1", shipping_code="VDX1",
        total_amount="1000",
    )
    # Đơn xuất DV20 -1 (xuất theo đơn).
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-1,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    return wh, order, p1, p2


def test_issue_doc_appears_in_orders_page_with_breakdown():
    wh, order, p1, _ = _seed_docs()
    doc = _post_issue_doc(wh, p1, 1, note="gửi lại đổi hàng lỗi")

    data = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    kinds = {i["kind"] for i in data["items"]}
    assert kinds == {"order", "document"}
    doc_row = next(i for i in data["items"] if i["kind"] == "document")
    assert doc_row["doc_code"] == doc.code
    assert doc_row["order_id"] is None
    assert doc_row["net"] == 1
    assert doc_row["sku_count"] == 1
    assert doc_row["lines"] == [{"product_id": p1.id, "sku": "DV20", "qty": 1}]

    # 1 xuất theo đơn + 1 phiếu kho = 2, breakdown tách bạch.
    assert data["sale_quantity"] == 1
    assert data["doc_quantity"] == 1
    assert data["total_quantity"] == 2
    assert data["total"] == 2  # 1 đơn + 1 phiếu


def test_cancelled_issue_doc_excluded():
    wh, _order, p1, _ = _seed_docs()
    doc = _post_issue_doc(wh, p1, 1)
    StockDocumentService.cancel_document(document_id=doc.id)

    data = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    assert all(i["kind"] != "document" for i in data["items"])
    assert data["doc_quantity"] == 0
    # Chỉ còn xuất theo đơn.
    assert data["total_quantity"] == 1
    assert data["total"] == 1


def test_skus_page_merges_doc_quantity():
    wh, _order, p1, _ = _seed_docs()
    _post_issue_doc(wh, p1, 3)

    data = MergedIssueHistory.skus_page(pagination={"limit": 50, "offset": 0})
    row = next(r for r in data["items"] if r["sku"] == "DV20")
    assert row["net"] == 1  # xuất theo đơn
    assert row["doc_quantity"] == 3  # phiếu kho
    assert row["doc_count"] == 1
    assert data["sale_quantity"] == 1
    assert data["doc_quantity"] == 3
    assert data["total_quantity"] == 4
    assert data["total"] == 1  # cùng 1 SKU (DV20)


def test_skus_page_doc_only_product():
    wh, _order, _p1, p2 = _seed_docs()
    _post_issue_doc(wh, p2, 2)  # DV21 chỉ xuất qua phiếu kho, không có đơn

    data = MergedIssueHistory.skus_page(pagination={"limit": 50, "offset": 0})
    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["DV21"]["net"] == 0
    assert by_sku["DV21"]["doc_quantity"] == 2
    assert data["total"] == 2  # DV20 (đơn) + DV21 (chỉ phiếu)


def test_sku_drill_includes_documents():
    wh, order, p1, _ = _seed_docs()
    doc = _post_issue_doc(wh, p1, 4)

    rows = MergedIssueHistory.sku_drill(product_id=p1.id)
    by_kind = {r["kind"]: r for r in rows}
    assert set(by_kind) == {"order", "document"}
    assert by_kind["order"]["order_id"] == order.id
    assert by_kind["order"]["net"] == 1
    assert by_kind["document"]["doc_code"] == doc.code
    assert by_kind["document"]["net"] == 4
    assert by_kind["document"]["order_id"] is None


def test_transfer_and_adjustment_not_counted():
    wh, _order, p1, _ = _seed_docs()
    # Bút toán khác loại (không phải phiếu Xuất kho) tham chiếu document → PHẢI bỏ qua.
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.TRANSFER_OUT, reference_type="document", reference_id=999,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-7,
        movement_type=MovementType.ADJUSTMENT, reference_type="document", reference_id=998,
    )
    assert IssueDocumentHistoryService.total_issued() == 0
    data = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    assert data["doc_quantity"] == 0


def test_order_search_matches_doc_code_and_note():
    wh, _order, p1, _ = _seed_docs()
    doc = _post_issue_doc(wh, p1, 1, note="đổi hàng lỗi 84859680911619")

    # Khớp theo ghi chú phiếu.
    hit = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, order="84859680911619"
    )
    assert any(i["kind"] == "document" and i["doc_code"] == doc.code for i in hit["items"])

    # Mã không liên quan → phiếu ẩn.
    miss = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, order="ZZZ-KHONG-CO"
    )
    assert all(i["kind"] != "document" for i in miss["items"])


def test_product_filter_applies_to_docs():
    wh, _order, p1, p2 = _seed_docs()
    _post_issue_doc(wh, p1, 1)  # DV20
    _post_issue_doc(wh, p2, 9)  # DV21

    data = MergedIssueHistory.skus_page(
        pagination={"limit": 50, "offset": 0}, product="DV21"
    )
    assert {r["sku"] for r in data["items"]} == {"DV21"}
    assert data["doc_quantity"] == 9


def test_merged_orders_pagination_orders_by_time():
    wh, order, p1, _ = _seed_docs()
    doc = _post_issue_doc(wh, p1, 1)
    # Ép thứ tự thời gian: phiếu MỚI hơn đơn. Đơn xuất theo `occurred_at`
    # (Task 5) nên backdate cả hai cột, giữ đúng bất biến occurred_at <= created_at.
    StockMovement.objects.filter(reference_type="order", reference_id=order.id).update(
        created_at="2026-07-01T00:00:00Z", occurred_at="2026-07-01T00:00:00Z"
    )
    StockMovement.objects.filter(reference_type="document", reference_id=doc.id).update(
        created_at="2026-07-10T00:00:00Z", occurred_at="2026-07-10T00:00:00Z"
    )
    page1 = MergedIssueHistory.orders_page(pagination={"limit": 1, "offset": 0})
    page2 = MergedIssueHistory.orders_page(pagination={"limit": 1, "offset": 1})
    assert page1["total"] == 2
    assert len(page1["items"]) == 1 and page1["items"][0]["kind"] == "document"
    assert len(page2["items"]) == 1 and page2["items"][0]["kind"] == "order"


def test_performed_by_from_doc_poster():
    wh, _order, p1, _ = _seed_docs()
    approver = User.objects.create_user(
        username="kt", password="x", display_name="Kế Toán"
    )
    _post_issue_doc(wh, p1, 1, user=approver)
    data = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    doc_row = next(i for i in data["items"] if i["kind"] == "document")
    assert doc_row["performed_by"] == "Kế Toán"


def test_skus_page_backfills_order_qty_for_sku_outside_window():
    """SKU rớt khỏi top-window theo đơn nhưng có phiếu kho mới: dòng phải mang ĐỦ
    số xuất-theo-đơn, không hiện net=0 tự mâu thuẫn với footer (audit 2026-07-15b).

    Dựng: DV20 xuất theo đơn TRƯỚC (last_at cũ), DV21 xuất theo đơn SAU (last_at
    mới). window=1 → order_head chỉ còn DV21. DV20 có phiếu kho mới nhất nên vẫn
    nổi lên trang — trước fix dòng này hiện dispatched/net = 0.
    """
    wh, order, p1, p2 = _seed_docs()  # p1=DV20 đã xuất theo đơn -1 trong _seed_docs
    # DV21 xuất theo đơn SAU → chiếm slot window đầu tiên.
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p2.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    # Phiếu kho MỚI NHẤT cho DV20 → last_at phiếu đẩy DV20 lên đầu trang.
    _post_issue_doc(wh, p1, 3)

    data = MergedIssueHistory.skus_page(pagination={"limit": 1, "offset": 0})
    assert data["items"], "trang đầu phải có dòng"
    top = data["items"][0]
    assert top["sku"] == "DV20", "last_at phiếu mới nhất phải đứng đầu"
    assert top["doc_quantity"] == 3
    assert top["dispatched"] == 1, "số xuất theo đơn phải được truy bù, không để 0"
    assert top["net"] == 1
    assert top["order_count"] == 1


# ---------------------------------------------------------------------------
# Total/tile của tab Xuất phải khớp đúng số dòng bảng liệt kê.
#
# - ``MergedIssueHistory``: total đếm trên `base_queryset` (còn cả đơn/SKU chỉ
#   có bút toán đảo trong kỳ) trong khi bảng đã lọc `_HAS_REAL_SALE` → "1 đơn"
#   với bảng trống, trang cuối trống.
# - Đơn un-dispatch trong CÙNG kỳ (xuất nhầm rồi bỏ đánh dấu, issued = 0): tile
#   "ĐƠN ĐÃ XUẤT" bỏ qua nhưng bảng vẫn liệt kê và FE hiện SL dương — ba con số
#   trên một màn hình nói ba kiểu. Hàng thực CHƯA rời kho → không được ra bảng.
# (gốc: test_issue_history_totals.py)
# ---------------------------------------------------------------------------


def _seed_totals():
    ch = Channel.objects.create(code="lp-tot", name="TOT")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000020", phone_normalized="0900000020"
    )
    wh = Warehouse.objects.create(code="WH-TOT", name="Kho T")
    p1 = Product.objects.create(sku="TOT1", name="SP tot 1")
    p2 = Product.objects.create(sku="TOT2", name="SP tot 2")

    def make_order(suffix):
        return Order.objects.create(
            channel=ch, customer=cust, name="K", phone="0900000020",
            order_code=f"DHT{suffix}", invoice_code=f"HDT{suffix}",
            shipping_code=f"VDT{suffix}", total_amount="1000",
        )

    return wh, p1, p2, make_order


def test_reversal_only_order_not_counted_in_merged_total():
    wh, p1, p2, make_order = _seed_totals()
    real = make_order("1")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-1,
        movement_type=MovementType.SALE, reference_type="order",
        reference_id=real.id,
    )
    # Đơn xuất kỳ TRƯỚC, kỳ này chỉ có bút toán đảo huỷ đơn (trên SKU khác).
    ghost = make_order("2")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p2.id, qty_delta=1,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order",
        reference_id=ghost.id, note=CANCEL_RELEASE_NOTE,
    )

    orders = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    assert len(orders["items"]) == 1
    assert orders["total"] == 1, "total phải bằng số dòng bảng liệt kê"

    skus = MergedIssueHistory.skus_page(pagination={"limit": 50, "offset": 0})
    assert len(skus["items"]) == 1
    assert skus["total"] == 1


def test_undispatched_same_period_order_is_excluded_everywhere():
    wh, p1, _, make_order = _seed_totals()
    order = make_order("3")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order",
        reference_id=order.id,
    )
    # Bỏ đánh dấu "Đã xuất kho" (note rỗng): hàng thực CHƯA rời kho.
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=3,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order",
        reference_id=order.id, note="",
    )

    page = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    assert page["items"] == []
    assert page["total"] == 0
    assert page["total_quantity"] == 0

    summary = FlowSummaryService.summary()
    assert summary["issue"]["order_count"] == 0

    merged = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    assert merged["items"] == []
    assert merged["total"] == 0


# ---------------------------------------------------------------------------
# Combo đã chuyển sổ sang mã lẻ (backfill 0038 / lệnh backfill_combo_stock).
# Sổ ghi cặp: SALE_REVERSAL trên SKU combo + SALE trên từng mã lẻ, cả hai giữ
# `occurred_at` của bút toán gốc. Xem docs/combo-stock-expansion-2026-08-20.md.
# ---------------------------------------------------------------------------


def _seed_combo_transferred(moved: int = 1, sold: int = 1):
    """Dựng đúng trạng thái prod sau backfill: combo bán `sold`, chuyển `moved`."""
    channel = Channel.objects.create(code="lp-cbh", name="CBH")
    customer = Customer.objects.create(
        full_name="K CB", phone_raw="0900000077", phone_normalized="0900000077"
    )
    wh = Warehouse.objects.create(code="WH-CBH", name="Kho Combo")
    combo = Product.objects.create(sku="CB-COMBO", name="Combo A+B")
    part_a = Product.objects.create(sku="CB-PA", name="Phần A")
    part_b = Product.objects.create(sku="CB-PB", name="Phần B")
    order = Order.objects.create(
        channel=channel, customer=customer, order_code="DH-CB", invoice_code="HD-CB",
    )
    ref = {"reference_type": "order", "reference_id": order.id}
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=-sold,
        movement_type=MovementType.SALE, **ref,
    )
    if moved:
        StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=combo.id, qty_delta=moved,
            movement_type=MovementType.SALE_REVERSAL,
            note=COMBO_BACKFILL_RELEASE_NOTE, **ref,
        )
        for part in (part_a, part_b):
            StockCommandService.record_movement(
                warehouse_id=wh.id, product_id=part.id, qty_delta=-moved,
                movement_type=MovementType.SALE, note=COMBO_BACKFILL_ISSUE_NOTE, **ref,
            )
    return wh, order, combo, part_a, part_b


def test_skus_page_an_dong_combo_da_chuyen_so_het():
    """Combo chuyển sổ HẾT không còn là một dòng xuất — hàng đã ghi ở mã lẻ.

    Không ẩn thì `display_qty` lấy `gross` khi net = 0 và dòng combo vẫn hiện SL,
    tức cùng một kiện hàng bị đếm hai lần (ca prod 20/08 sau khi chạy backfill).
    """
    _seed_combo_transferred()

    data = MergedIssueHistory.skus_page(pagination={"limit": 50, "offset": 0})

    by_sku = {r["sku"]: r for r in data["items"]}
    assert "CB-COMBO" not in by_sku
    assert by_sku["CB-PA"]["net"] == 1
    assert by_sku["CB-PB"]["net"] == 1


def test_skus_page_combo_chuyen_so_mot_phan_van_hien_phan_con_lai():
    """Khai thành phần muộn: phần cũ chưa chuyển vẫn phải hiện trên SKU combo."""
    _seed_combo_transferred(moved=1, sold=3)

    data = MergedIssueHistory.skus_page(pagination={"limit": 50, "offset": 0})

    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["CB-COMBO"]["net"] == 2
    assert by_sku["CB-COMBO"]["dispatched"] == 2
    assert by_sku["CB-PA"]["net"] == 1


def test_but_toan_nhan_so_tu_combo_tinh_vao_san_luong_xuat():
    """Bút toán nhận sổ giữ `occurred_at` GỐC nên là hàng thật rời kho ngày đó —
    khác các note trong ADMIN_SALE_NOTES vốn mang mốc lúc ghi."""
    _seed_combo_transferred()

    data = MergedIssueHistory.skus_page(pagination={"limit": 50, "offset": 0})

    # 1 combo rời kho = 2 món ở mã lẻ; tile tổng phải nói đúng con số đó.
    assert data["sale_quantity"] == 2


def test_orders_page_an_dong_combo_da_chuyen_so_het():
    """Chế độ Theo đơn: dòng SKU combo đã chuyển sổ hết phải biến mất.

    Ca prod SPXVN066980553678 (20/08): sổ giữ combo (SALE −1 + đảo chuyển sổ +1)
    kèm 2 mã lẻ. Dòng combo có net = 0 nên `display_qty` rơi về `gross` = 1, rồi
    file Excel lại nở nó ra chính 2 mã lẻ đó — mỗi mã hiện HAI dòng.
    """
    _seed_combo_transferred()

    page = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})

    [item] = page["items"]
    by_sku = {line["sku"]: line for line in item["lines"]}
    assert "CB-COMBO" not in by_sku
    assert by_sku["CB-PA"]["qty"] == 1
    assert by_sku["CB-PB"]["qty"] == 1
    # "Số SKU" đếm theo dòng còn lại, không tính SKU combo đã chuyển sổ.
    assert item["sku_count"] == 2


def test_orders_page_combo_chuyen_so_mot_phan_giu_phan_con_lai():
    """Khai thành phần muộn: phần chưa chuyển vẫn là một dòng combo có SL."""
    _seed_combo_transferred(moved=1, sold=3)

    page = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})

    [item] = page["items"]
    by_sku = {line["sku"]: line for line in item["lines"]}
    assert by_sku["CB-COMBO"]["qty"] == 2
    # `gross` cũng phải trừ phần đã chuyển sổ, nếu không đơn hoàn/huỷ rơi về
    # fallback `gross` sẽ báo lại cả phần đã tính ở mã lẻ.
    assert by_sku["CB-COMBO"]["gross"] == 2
    assert by_sku["CB-PA"]["qty"] == 1
    assert item["sku_count"] == 3
