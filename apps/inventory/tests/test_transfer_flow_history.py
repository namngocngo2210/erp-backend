"""Phiếu CHUYỂN KHO trên trang Lịch sử nhập xuất.

transfer_out hiện ở tab Xuất, transfer_in hiện ở tab Nhập hoàn — mỗi bên là
bucket riêng (transfer_quantity), có breakdown tách bạch khỏi xuất theo đơn /
phiếu XK / SL hoàn.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.issue_document_history import (
    IssueDocumentHistoryService,
    MergedIssueHistory,
)
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.application.services.transfer_document_history import (
    MergedReturnHistory,
    TransferDocumentHistoryService,
)
from apps.inventory.models import DocType, MovementType, ReturnType
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.users.models import User
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _post_transfer(src, dest, lines, *, user=None, note="", occurred_at=None):
    """Tạo + duyệt 1 phiếu chuyển kho → movement transfer_out/in per SKU."""
    uid = user.id if user else None
    doc = StockDocumentService.create_document(
        doc_type=DocType.TRANSFER,
        source_warehouse_id=src.id,
        dest_warehouse_id=dest.id,
        lines=lines,
        note=note,
        user_id=uid,
        created_at=occurred_at,
    )
    StockDocumentService.submit_for_approval(document_id=doc.id, user_id=uid)
    StockDocumentService.approve_document(document_id=doc.id, user_id=uid)
    return doc


def _seed():
    ch = Channel.objects.create(code="lp-tr", name="TR")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000020", phone_normalized="0900000020"
    )
    src = Warehouse.objects.create(code="WH-SRC", name="Kho Nguồn")
    dest = Warehouse.objects.create(code="WH-DST", name="Kho Đích")
    p1 = Product.objects.create(sku="TR01", name="SP một")
    p2 = Product.objects.create(sku="TR02", name="SP hai")
    order = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000020",
        order_code="DHT1", invoice_code="HDT1", shipping_code="VDT1",
        total_amount="1000",
    )
    # 1 xuất theo đơn để tab Xuất có sẵn nguồn "order".
    StockCommandService.record_movement(
        warehouse_id=src.id, product_id=p1.id, qty_delta=-1,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    return src, dest, order, p1, p2


# ─────────────────────────── Tab Xuất (transfer_out) ───────────────────────────


def test_transfer_appears_in_issue_orders_page_with_breakdown():
    src, dest, _order, p1, p2 = _seed()
    doc = _post_transfer(
        src, dest, [{"product_id": p1.id, "quantity": 7}, {"product_id": p2.id, "quantity": 3}]
    )

    data = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    kinds = {i["kind"] for i in data["items"]}
    assert kinds == {"order", "transfer"}
    row = next(i for i in data["items"] if i["kind"] == "transfer")
    assert row["doc_code"] == doc.code
    assert row["order_id"] is None
    assert row["net"] == 10
    assert row["sku_count"] == 2
    assert row["warehouse_name"] == "Kho Nguồn → Kho Đích"
    assert {(line["sku"], line["qty"]) for line in row["lines"]} == {("TR01", 7), ("TR02", 3)}

    # Breakdown: theo đơn 1 + phiếu kho 0 + chuyển kho 10.
    assert data["sale_quantity"] == 1
    assert data["doc_quantity"] == 0
    assert data["transfer_quantity"] == 10
    assert data["total_quantity"] == 11
    assert data["total"] == 2  # 1 đơn + 1 phiếu chuyển


def test_cancelled_transfer_excluded_both_tabs():
    src, dest, _order, p1, _p2 = _seed()
    doc = _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 5}])
    StockDocumentService.cancel_document(document_id=doc.id)

    issue = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    assert all(i["kind"] != "transfer" for i in issue["items"])
    assert issue["transfer_quantity"] == 0

    ret = MergedReturnHistory.list_page(pagination={"limit": 50, "offset": 0})
    assert all(i["kind"] != "transfer" for i in ret["items"])
    assert ret["transfer_quantity"] == 0


def test_cancelled_transfer_excluded_when_cancelled_on_another_day():
    """Phiếu ghi sổ ngày D, huỷ ngày D+1 → KHÔNG dòng nào ở cả hai ngày.

    Bút toán đảo mang mốc lúc HUỶ, còn bút toán gốc mang mốc nghiệp vụ của phiếu:
    lọc theo 1 ngày chỉ nhặt được một vế nên "net tự về 0" không cứu được — ngày
    huỷ hiện dòng SL ÂM (ca prod CK20260818-0001, Excel ra -1).
    """
    src, dest, _order, p1, _p2 = _seed()
    yesterday = timezone.now() - timedelta(days=1)
    doc = _post_transfer(
        src, dest, [{"product_id": p1.id, "quantity": 1}], occurred_at=yesterday
    )
    StockDocumentService.cancel_document(document_id=doc.id)

    today = timezone.localtime(timezone.now()).date().isoformat()
    day_before = timezone.localtime(yesterday).date().isoformat()
    for day in (today, day_before):
        window = {"date_from": day, "date_to": day}
        ret = MergedReturnHistory.list_page(
            pagination={"limit": 50, "offset": 0}, **window
        )
        assert all(i["kind"] != "transfer" for i in ret["items"]), day
        assert ret["transfer_quantity"] == 0, day
        assert TransferDocumentHistoryService.total_in(**window) == 0, day

        issue = MergedIssueHistory.orders_page(
            pagination={"limit": 50, "offset": 0}, **window
        )
        assert all(i["kind"] != "transfer" for i in issue["items"]), day
        assert issue["transfer_quantity"] == 0, day
        assert TransferDocumentHistoryService.total_out(**window) == 0, day


def test_transfer_not_in_issue_doc_bucket():
    """transfer_quantity KHÔNG lẫn vào doc_quantity (phiếu XK) — bucket độc lập."""
    src, dest, _order, p1, _p2 = _seed()
    _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 5}])
    assert IssueDocumentHistoryService.total_issued() == 0
    assert TransferDocumentHistoryService.total_out() == 5


def test_issue_warehouse_filter_matches_source_only():
    """Tab Xuất lọc theo kho: phiếu chuyển khớp KHO NGUỒN (nơi bị trừ tồn)."""
    src, dest, _order, p1, _p2 = _seed()
    _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 4}])

    hit = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, warehouse_ids=[src.id]
    )
    assert any(i["kind"] == "transfer" for i in hit["items"])
    assert hit["transfer_quantity"] == 4

    miss = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, warehouse_ids=[dest.id]
    )
    assert all(i["kind"] != "transfer" for i in miss["items"])
    assert miss["transfer_quantity"] == 0


def test_issue_skus_page_merges_transfer_quantity():
    src, dest, _order, p1, p2 = _seed()
    _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 3}])
    _post_transfer(src, dest, [{"product_id": p2.id, "quantity": 2}])

    data = MergedIssueHistory.skus_page(pagination={"limit": 50, "offset": 0})
    by_sku = {r["sku"]: r for r in data["items"]}
    # TR01 vừa xuất theo đơn vừa chuyển kho.
    assert by_sku["TR01"]["net"] == 1
    assert by_sku["TR01"]["transfer_quantity"] == 3
    assert by_sku["TR01"]["transfer_count"] == 1
    # TR02 chỉ chuyển kho — vẫn phải hiện dòng.
    assert by_sku["TR02"]["net"] == 0
    assert by_sku["TR02"]["transfer_quantity"] == 2
    assert data["transfer_quantity"] == 5
    assert data["total_quantity"] == 1 + 5
    assert data["total"] == 2


def test_issue_sku_drill_includes_transfer():
    src, dest, _order, p1, _p2 = _seed()
    doc = _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 6}])

    rows = MergedIssueHistory.sku_drill(product_id=p1.id)
    by_kind = {r["kind"]: r for r in rows}
    assert set(by_kind) == {"order", "transfer"}
    assert by_kind["transfer"]["doc_code"] == doc.code
    assert by_kind["transfer"]["net"] == 6
    assert by_kind["transfer"]["warehouse_name"] == "Kho Nguồn → Kho Đích"


def test_order_search_matches_transfer_code_and_note():
    src, dest, _order, p1, _p2 = _seed()
    doc = _post_transfer(
        src, dest, [{"product_id": p1.id, "quantity": 2}], note="chuyển bổ sung cửa hàng"
    )

    hit = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, order=doc.code
    )
    assert any(i["kind"] == "transfer" for i in hit["items"])

    hit_note = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, order="bổ sung cửa hàng"
    )
    assert any(i["kind"] == "transfer" for i in hit_note["items"])

    miss = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, order="ZZZ-KHONG-CO"
    )
    assert all(i["kind"] != "transfer" for i in miss["items"])


def test_performed_by_from_transfer_poster():
    src, dest, _order, p1, _p2 = _seed()
    approver = User.objects.create_user(username="kt2", password="x", display_name="Kế Toán 2")
    _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 1}], user=approver)

    data = MergedIssueHistory.orders_page(pagination={"limit": 50, "offset": 0})
    row = next(i for i in data["items"] if i["kind"] == "transfer")
    assert row["performed_by"] == "Kế Toán 2"


# ─────────────────────── Tab Nhập hoàn (transfer_in) ───────────────────────


def _post_return(order, warehouse, product, qty):
    """Tạo 1 nhóm đơn hoàn đã ghi sổ để tab Nhập hoàn có nguồn 'return'."""
    return ReturnEntryService.create_and_post_entry(
        warehouse_id=warehouse.id,
        source_order_id=order.id,
        lines=[
            {
                "product_id": product.id,
                "quantity": qty,
                "return_type": ReturnType.DELIVERY_RETURNED,
            }
        ],
    )


def test_transfer_appears_in_return_list_with_breakdown():
    src, dest, order, p1, _p2 = _seed()
    entry = _post_return(order, dest, p1, 1)
    doc = _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 7}])

    data = MergedReturnHistory.list_page(pagination={"limit": 50, "offset": 0})
    kinds = {i["kind"] for i in data["items"]}
    assert kinds == {"return", "transfer"}
    row = next(i for i in data["items"] if i["kind"] == "transfer")
    assert row["code"] == doc.code
    assert row["return_id"] is None
    assert row["qty_total"] == 7
    assert row["return_type_label"] == "Chuyển kho"
    assert row["warehouse_name"] == "Kho Nguồn → Kho Đích"
    assert row["lines"] == [{"product_id": p1.id, "sku": "TR01", "qty": 7}]

    ret_row = next(i for i in data["items"] if i["kind"] == "return")
    assert ret_row["return_id"] == entry.id

    assert data["return_quantity"] == 1
    assert data["transfer_quantity"] == 7
    assert data["total_quantity"] == 8
    assert data["total"] == 2


def test_return_warehouse_filter_matches_dest_only():
    """Tab Nhập hoàn lọc theo kho: phiếu chuyển khớp KHO ĐÍCH (nơi được cộng tồn)."""
    src, dest, _order, p1, _p2 = _seed()
    _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 4}])

    hit = MergedReturnHistory.list_page(
        pagination={"limit": 50, "offset": 0}, warehouse_ids=[dest.id]
    )
    assert any(i["kind"] == "transfer" for i in hit["items"])

    miss = MergedReturnHistory.list_page(
        pagination={"limit": 50, "offset": 0}, warehouse_ids=[src.id]
    )
    assert all(i["kind"] != "transfer" for i in miss["items"])


def test_return_skus_page_merges_transfer():
    src, dest, order, p1, p2 = _seed()
    _post_return(order, dest, p1, 1)
    _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 3}])
    _post_transfer(src, dest, [{"product_id": p2.id, "quantity": 5}])

    data = MergedReturnHistory.skus_page(pagination={"limit": 50, "offset": 0})
    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["TR01"]["qty_total"] == 1
    assert by_sku["TR01"]["transfer_quantity"] == 3
    assert by_sku["TR01"]["transfer_count"] == 1
    # TR02 chỉ có chuyển kho — vẫn hiện dòng.
    assert by_sku["TR02"]["qty_total"] == 0
    assert by_sku["TR02"]["transfer_quantity"] == 5
    assert data["return_quantity"] == 1
    assert data["transfer_quantity"] == 8
    assert data["total_quantity"] == 9
    assert data["total"] == 2


def test_return_sku_drill_includes_transfer():
    src, dest, order, p1, _p2 = _seed()
    _post_return(order, dest, p1, 1)
    doc = _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 6}])

    rows = MergedReturnHistory.sku_drill(product_id=p1.id)
    kinds = {r["kind"] for r in rows}
    assert kinds == {"return", "transfer"}
    tr = next(r for r in rows if r["kind"] == "transfer")
    assert tr["code"] == doc.code
    assert tr["quantity"] == 6
    assert tr["return_type_label"] == "Chuyển kho"


def test_product_filter_applies_to_transfer_rows():
    src, dest, _order, p1, p2 = _seed()
    _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 1}])
    _post_transfer(src, dest, [{"product_id": p2.id, "quantity": 9}])

    issue = MergedIssueHistory.skus_page(
        pagination={"limit": 50, "offset": 0}, product="TR02"
    )
    assert {r["sku"] for r in issue["items"]} == {"TR02"}
    assert issue["transfer_quantity"] == 9

    ret = MergedReturnHistory.skus_page(
        pagination={"limit": 50, "offset": 0}, product="TR02"
    )
    assert {r["sku"] for r in ret["items"]} == {"TR02"}
    assert ret["transfer_quantity"] == 9


def test_merged_return_pagination_orders_by_time():
    from apps.inventory.models import StockMovement

    src, dest, order, p1, _p2 = _seed()
    entry = _post_return(order, dest, p1, 1)
    doc = _post_transfer(src, dest, [{"product_id": p1.id, "quantity": 3}])
    # Ép thứ tự: phiếu chuyển MỚI hơn nhóm hoàn.
    type(entry).objects.filter(id=entry.id).update(created_at="2026-07-01T00:00:00Z")
    StockMovement.objects.filter(reference_type="document", reference_id=doc.id).update(
        created_at="2026-07-10T00:00:00Z"
    )
    page1 = MergedReturnHistory.list_page(pagination={"limit": 1, "offset": 0})
    page2 = MergedReturnHistory.list_page(pagination={"limit": 1, "offset": 1})
    assert page1["total"] == 2
    assert len(page1["items"]) == 1 and page1["items"][0]["kind"] == "transfer"
    assert len(page2["items"]) == 1 and page2["items"][0]["kind"] == "return"
