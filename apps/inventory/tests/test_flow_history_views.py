"""Test endpoint transport lịch sử nhập xuất — theo đơn, drill-down SKU, chờ khớp.

Phần test 2 endpoint export đồng bộ (`dispatch-history/export`, `return-history/
export`) đã chuyển sang `apps.inventory.tests.test_inventory_export_definitions`
(Task 18 — xoá 2 endpoint đó, xem
docs/.superpowers/sdd/2026-08-06-async-export-jobs/task-18-report.md).
"""
from __future__ import annotations

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.flow_filters import parse_warehouse_ids
from apps.inventory.models import (
    CancelledDispatchWatch,
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnStatus,
    ReturnType,
)
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product, ProductComponent
from apps.users.tests.helpers import make_admin, make_user_with_permissions
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_admin():
    c = APIClient()
    c.force_authenticate(make_admin("admin-flowhist"))
    return c


def _seed():
    channel = Channel.objects.create(code="lp-fh", name="FH")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000001", phone_normalized="0900000001"
    )
    wh = Warehouse.objects.create(code="WH-FH", name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="K",
        phone="0900000001",
        order_code="DH001",
        invoice_code="HD001",
        shipping_code="VD001",
        total_amount="1000",
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    # Nhóm đơn hoàn đã ghi sổ (1 dòng của tab Nhập hoàn).
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=order.id,
        lines=[
            {
                "product_id": p1.id,
                "quantity": 2,
                "return_type": ReturnType.DELIVERY_RETURNED,
            }
        ],
    )
    # Mã vận đơn chờ khớp (chưa gắn đơn).
    PendingShipmentScan.objects.create(
        code="VDUNMATCHED", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    PendingShipmentScan.objects.create(
        code="VDRET", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.FAILED, error="lỗi",
    )
    return wh, order, p1, entry


def test_issue_orders_endpoint(client_admin):
    wh, order, p1, rn = _seed()
    r = client_admin.get("/api/inventory/dispatch-history/orders?limit=50&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["total_quantity"] == 5
    assert body["items"][0]["order_code"] == "DH001"
    assert body["items"][0]["invoice_code"] == "HD001"


@pytest.mark.parametrize(
    "url",
    ["/api/inventory/dispatch-history/orders", "/api/inventory/return-history"],
    ids=["dispatch_orders", "return_history"],
)
def test_list_endpoints_return_lines(client_admin, url):
    """Payload danh sách (tab Xuất theo đơn / tab Nhập hoàn) phải kèm `lines` — SKU x
    số lượng rút gọn hiển thị ngay trong cell mã hóa đơn; cùng shape, dùng chung
    component OpenAPI FlowLineBrief."""
    _seed()
    r = client_admin.get(f"{url}?limit=50&offset=0")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert isinstance(item["lines"], list)
    assert {"product_id", "sku", "qty"} <= set(item["lines"][0])


@pytest.mark.parametrize(
    ("url", "code", "status"),
    [
        ("/api/inventory/dispatch-history/pending", "VDUNMATCHED", "pending"),
        ("/api/inventory/return-history/pending", "VDRET", "failed"),
    ],
    ids=["dispatch_pending", "return_pending"],
)
def test_pending_endpoints(client_admin, url, code, status):
    _seed()
    r = client_admin.get(url)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["code"] == code
    assert items[0]["status"] == status


def test_issue_pending_includes_matched_with_matched_at(client_admin):
    """Dòng đã khớp vẫn hiện trên trang, kèm matched_at + matched_order_code."""
    wh, order, p1, rn = _seed()
    PendingShipmentScan.objects.create(
        code="VDMATCHED", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.MATCHED, matched_order=order,
    )
    items = client_admin.get("/api/inventory/dispatch-history/pending").json()["items"]
    by_code = {it["code"]: it for it in items}
    # Cả dòng chưa khớp lẫn đã khớp đều hiện.
    assert "VDUNMATCHED" in by_code and "VDMATCHED" in by_code
    matched = by_code["VDMATCHED"]
    assert matched["status"] == "matched"
    assert matched["matched_at"] is not None
    assert matched["matched_order_code"] == order.order_code
    # Dòng chưa khớp không có thời điểm khớp.
    assert by_code["VDUNMATCHED"]["matched_at"] is None
    assert by_code["VDUNMATCHED"]["matched_order_code"] == ""


def test_issue_pending_match_filter(client_admin):
    """?match=matched|unmatched|all lọc dòng chờ khớp theo trạng thái khớp."""
    wh, order, p1, rn = _seed()
    PendingShipmentScan.objects.create(
        code="VDMATCHED", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.MATCHED, matched_order=order,
    )
    base = "/api/inventory/dispatch-history/pending"

    codes_all = {it["code"] for it in client_admin.get(f"{base}?match=all").json()["items"]}
    assert codes_all == {"VDUNMATCHED", "VDMATCHED"}

    matched = client_admin.get(f"{base}?match=matched").json()["items"]
    assert {it["code"] for it in matched} == {"VDMATCHED"}

    unmatched = client_admin.get(f"{base}?match=unmatched").json()["items"]
    assert {it["code"] for it in unmatched} == {"VDUNMATCHED"}

    # match không hợp lệ / thiếu → mặc định 'all'.
    codes_default = {it["code"] for it in client_admin.get(base).json()["items"]}
    assert codes_default == {"VDUNMATCHED", "VDMATCHED"}
    codes_bad = {it["code"] for it in client_admin.get(f"{base}?match=xxx").json()["items"]}
    assert codes_bad == {"VDUNMATCHED", "VDMATCHED"}


def test_issue_pending_filtered_by_order_code(client_admin):
    """Ô tìm mã (order) lọc luôn khối mã chờ khớp: chỉ giữ mã quét khớp theo
    chính code hoặc theo mã đơn đã khớp; mã không liên quan bị loại."""
    wh, order, p1, rn = _seed()
    PendingShipmentScan.objects.create(
        code="ZZMATCHED", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.MATCHED, matched_order=order,
    )
    base = "/api/inventory/dispatch-history/pending"
    # Tìm theo code của chính mã quét.
    codes = {it["code"] for it in client_admin.get(f"{base}?order=VDUNMATCHED").json()["items"]}
    assert codes == {"VDUNMATCHED"}
    # Tìm theo mã hóa đơn của đơn đã khớp → chỉ dòng đã khớp.
    codes = {it["code"] for it in client_admin.get(f"{base}?order=HD001").json()["items"]}
    assert codes == {"ZZMATCHED"}
    # Mã không tồn tại → rỗng.
    assert client_admin.get(f"{base}?order=ZZZNONE").json()["items"] == []


def test_issue_pending_hidden_when_product_filter(client_admin):
    """Lọc theo sản phẩm: mã chờ khớp chưa gắn thông tin SP → ẩn hẳn khối."""
    _seed()
    base = "/api/inventory/dispatch-history/pending"
    assert client_admin.get(f"{base}?product=SKU1").json()["items"] == []
    # Không lọc SP thì vẫn hiện như cũ.
    assert len(client_admin.get(base).json()["items"]) == 1


def test_return_pending_filtered_by_order_code(client_admin):
    """Tab Nhập hoàn: khối mã chờ khớp cũng lọc theo ô tìm mã + ẩn khi lọc SP."""
    wh, order, p1, rn = _seed()
    PendingShipmentScan.objects.create(
        code="ZZRETMATCH", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.MATCHED, matched_order=order,
    )
    base = "/api/inventory/return-history/pending"
    codes = {it["code"] for it in client_admin.get(f"{base}?order=VDRET").json()["items"]}
    assert codes == {"VDRET"}
    codes = {it["code"] for it in client_admin.get(f"{base}?order=HD001").json()["items"]}
    assert codes == {"ZZRETMATCH"}
    assert client_admin.get(f"{base}?product=SKU1").json()["items"] == []


def test_return_history_endpoint(client_admin):
    wh, order, p1, entry = _seed()
    r = client_admin.get("/api/inventory/return-history?limit=50&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["total_quantity"] == 2
    row = body["items"][0]
    assert row["return_id"] == entry.id  # id NHÓM ĐƠN (khớp return_id của mã quét)
    assert row["code"] == entry.return_note.code
    assert row["code"].startswith("PTH-")
    assert row["source_order_invoice_code"] == "HD001"


def test_return_note_update_endpoint_patches_entry_note(client_admin):
    """PATCH .../return-history/{return_id}/note nhận id NHÓM ĐƠN."""
    wh, order, p1, entry = _seed()
    r = client_admin.patch(
        f"/api/inventory/return-history/{entry.id}/note",
        {"note": "khách báo lỗi khóa kéo"},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json() == {"return_id": entry.id, "note": "khách báo lỗi khóa kéo"}
    entry.refresh_from_db()
    assert entry.note == "khách báo lỗi khóa kéo"


def test_return_note_update_endpoint_404_when_entry_missing(client_admin):
    wh, order, p1, entry = _seed()
    r = client_admin.patch(
        f"/api/inventory/return-history/{entry.id + 9999}/note",
        {"note": "x"},
        format="json",
    )
    assert r.status_code == 404


def test_return_note_update_endpoint_rejects_non_string(client_admin):
    wh, order, p1, entry = _seed()
    r = client_admin.patch(
        f"/api/inventory/return-history/{entry.id}/note", {"note": 5}, format="json"
    )
    assert r.status_code == 400


def test_return_pending_matched_exposes_return_note(client_admin):
    """Dòng mã đã khớp (tab Nhập hoàn) gắn return_id + note của NHÓM ĐƠN cùng
    mã vận đơn để sửa ghi chú ngay trên dòng."""
    wh, order, p1, rn = _seed()
    # Nhóm đơn có mã vận đơn + ghi chú, và 1 mã quét đã khớp cùng mã vận đơn đó.
    rn.shipping_code = "VDMATCH"
    rn.note = "khách báo lỗi khóa kéo"
    rn.save(update_fields=["shipping_code", "note"])
    PendingShipmentScan.objects.create(
        code="VDMATCH", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.MATCHED, matched_order=order,
    )
    items = client_admin.get(
        "/api/inventory/return-history/pending?match=all"
    ).json()["items"]
    by_code = {it["code"]: it for it in items}
    matched = by_code["VDMATCH"]
    assert matched["return_id"] == rn.id
    assert matched["note"] == "khách báo lỗi khóa kéo"
    # Dòng chưa khớp không gắn phiếu trả.
    assert by_code["VDRET"]["return_id"] is None
    assert by_code["VDRET"]["note"] == ""


def test_return_pending_matched_ignores_cancelled_return(client_admin):
    """Phiếu trả đã huỷ không được gắn vào dòng mã đã khớp."""
    wh, order, p1, rn = _seed()
    rn.shipping_code = "VDCANCEL"
    rn.status = ReturnStatus.CANCELLED
    rn.note = "phiếu huỷ"
    rn.save(update_fields=["shipping_code", "status", "note"])
    PendingShipmentScan.objects.create(
        code="VDCANCEL", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.MATCHED, matched_order=order,
    )
    items = client_admin.get(
        "/api/inventory/return-history/pending?match=matched"
    ).json()["items"]
    by_code = {it["code"]: it for it in items}
    assert by_code["VDCANCEL"]["return_id"] is None
    assert by_code["VDCANCEL"]["note"] == ""


def test_flow_order_lookup_by_invoice(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/flow-order-lookup?q=HD001")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["invoice_code"] == "HD001" for it in items)
    assert all("kiotviet_channel" in it for it in items)


def test_flow_order_lookup_masks_phone(client_admin):
    """Khớp thủ công chỉ cần đối chiếu SĐT, không cần lộ toàn bộ số (task 2.6)."""
    _seed()
    r = client_admin.get("/api/inventory/flow-order-lookup?q=HD001")
    assert r.status_code == 200
    items = r.json()["items"]
    match = next(it for it in items if it["invoice_code"] == "HD001")
    assert match["phone"] == "*******001"
    assert "0900000001" not in match["phone"]


def test_flow_manual_match_dispatch(client_admin):
    channel = Channel.objects.create(code="lp-mm", name="MM")
    customer = Customer.objects.create(
        full_name="KM", phone_raw="0900000055", phone_normalized="0900000055"
    )
    wh = Warehouse.objects.create(code="WH-MM", name="Kho MM")
    p = Product.objects.create(sku="SKU-MM", name="SP MM")
    order = Order.objects.create(
        channel=channel, customer=customer, name="KM", phone="0900000055",
        total_amount="1000", status=OrderStatus.AWAITING_SHIPMENT,
        shipping_code="SPXMM", fulfillment_warehouse=wh, invoice_code="HDMM",
    )
    OrderItem.objects.create(
        order=order, product=p, product_name=p.name, sku=p.sku,
        quantity=2, unit_price=1000, line_total=2000,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=2,
        movement_type=MovementType.RECEIPT,
    )
    scan = PendingShipmentScan.objects.create(
        code="UNMATCHEDMM", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    r = client_admin.post(
        f"/api/inventory/flow-pending/{scan.id}/match", {"order_id": order.id}
    )
    assert r.status_code == 200, r.content
    assert r.json()["ok"] is True
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED


def test_flow_manual_match_missing_order_id(client_admin):
    wh = Warehouse.objects.create(code="WH-MM2", name="Kho MM2")
    scan = PendingShipmentScan.objects.create(
        code="X", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    r = client_admin.post(f"/api/inventory/flow-pending/{scan.id}/match", {})
    assert r.status_code == 400


def test_flow_pending_mark_invalid(client_admin):
    wh = Warehouse.objects.create(code="WH-INV", name="Kho INV")
    scan = PendingShipmentScan.objects.create(
        code="BADCODE", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    r = client_admin.post(f"/api/inventory/flow-pending/{scan.id}/mark-invalid")
    assert r.status_code == 200
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.INVALID
    # Vẫn hiển thị trên danh sách chờ khớp (với badge Mã không đúng).
    lst = client_admin.get("/api/inventory/dispatch-history/pending").json()["items"]
    assert any(it["code"] == "BADCODE" and it["status"] == "invalid" for it in lst)


def test_flow_pending_delete(client_admin):
    wh = Warehouse.objects.create(code="WH-DEL", name="Kho DEL")
    scan = PendingShipmentScan.objects.create(
        code="DELME", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    r = client_admin.delete(f"/api/inventory/flow-pending/{scan.id}")
    assert r.status_code == 204
    assert not PendingShipmentScan.objects.filter(id=scan.id).exists()


def test_flow_pending_delete_matched_forbidden(client_admin):
    wh = Warehouse.objects.create(code="WH-DEL2", name="Kho DEL2")
    scan = PendingShipmentScan.objects.create(
        code="KEEP", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.MATCHED,
    )
    r = client_admin.delete(f"/api/inventory/flow-pending/{scan.id}")
    assert r.status_code == 400
    assert PendingShipmentScan.objects.filter(id=scan.id).exists()


def test_issue_skus_endpoint(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/dispatch-history/skus?limit=50&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["sku"] == "SKU1"
    assert body["items"][0]["order_count"] == 1
    assert body["items"][0]["net"] == 5
    assert body["total_quantity"] == 5


def test_issue_sku_orders_endpoint(client_admin):
    _seed()
    p = Product.objects.get(sku="SKU1")
    r = client_admin.get(
        f"/api/inventory/dispatch-history/skus/orders?product_id={p.id}"
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["invoice_code"] == "HD001"
    assert items[0]["net"] == 5


def test_issue_sku_orders_requires_product_id(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/dispatch-history/skus/orders")
    assert r.status_code == 400


def test_return_skus_endpoint(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/return-history/skus?limit=50&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["sku"] == "SKU1"
    assert body["items"][0]["return_count"] == 1
    assert body["items"][0]["qty_total"] == 2


def test_return_sku_returns_endpoint(client_admin):
    _seed()
    p = Product.objects.get(sku="SKU1")
    r = client_admin.get(
        f"/api/inventory/return-history/skus/returns?product_id={p.id}"
    )
    assert r.status_code == 200
    assert r.json()["items"][0]["quantity"] == 2


# ── flow_manual_match: gate theo kind (task 2.4; siết :edit sweep N2 đợt 2) ──
# Xuất kho (dispatch) và ghi phiếu trả (return) là 2 hành động khác nhau về
# quyền hạn dù cùng chung 1 endpoint — phải soi thêm đúng section như luồng
# quét chuẩn (pending_scan_views.py="shipments", return_views.py="returns").
# Contract mới (sweep N2 đợt 2): decorator đòi any-of (stock:edit, shipments:edit,
# returns:edit); fixture mô phỏng role prod SAU migration grandfather 0039 (role
# có :view thì có :edit tương ứng), gate theo kind bên trong vẫn soi :view của
# section hành động.


def _stock_only_client(username: str) -> APIClient:
    # Grandfather: role có stock:view nhận stock:edit (0039) — vẫn KHÔNG có
    # section hành động (shipments/returns) nên gate theo kind phải chặn.
    user = make_user_with_permissions(username, ["stock:view", "stock:edit"])
    c = APIClient()
    c.force_authenticate(user)
    return c


def _minimal_order(code_suffix: str) -> Order:
    channel = Channel.objects.create(code=f"lp-{code_suffix}", name=code_suffix)
    customer = Customer.objects.create(
        full_name="MM", phone_raw="0900000099", phone_normalized="0900000099"
    )
    return Order.objects.create(
        channel=channel, customer=customer, name="MM", phone="0900000099",
        order_code=f"DH-{code_suffix}", invoice_code=f"HD-{code_suffix}",
        shipping_code=f"VD-{code_suffix}", total_amount="1000",
    )


def test_flow_manual_match_dispatch_forbidden_without_shipments():
    wh = Warehouse.objects.create(code="WH-GATE1", name="Kho gate1")
    scan = PendingShipmentScan.objects.create(
        code="GATE-DISP", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    order = _minimal_order("gate1")
    client = _stock_only_client("stock-only-gate1")

    r = client.post(f"/api/inventory/flow-pending/{scan.id}/match", {"order_id": order.id})

    assert r.status_code == 403
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING


def test_flow_manual_match_return_forbidden_without_returns():
    wh = Warehouse.objects.create(code="WH-GATE2", name="Kho gate2")
    scan = PendingShipmentScan.objects.create(
        code="GATE-RET", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    order = _minimal_order("gate2")
    client = _stock_only_client("stock-only-gate2")

    r = client.post(f"/api/inventory/flow-pending/{scan.id}/match", {"order_id": order.id})

    assert r.status_code == 403
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING


def test_flow_manual_match_view_only_forbidden_after_sweep():
    """Contract mới sweep N2 đợt 2: chỉ có :view (không :edit nào) → 403 ngay ở
    decorator. Role prod không rơi vào ca này nhờ grandfather 0039; ca này là
    trạng thái SAU KHI admin thu hồi :edit trên UI — thu hồi phải có tác dụng."""
    wh = Warehouse.objects.create(code="WH-GATE5", name="Kho gate5")
    scan = PendingShipmentScan.objects.create(
        code="GATE-VIEWONLY", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    order = _minimal_order("gate5")
    user = make_user_with_permissions(
        "view-only-gate5", ["stock:view", "shipments:view", "returns:view"]
    )
    client = APIClient()
    client.force_authenticate(user)

    r = client.post(f"/api/inventory/flow-pending/{scan.id}/match", {"order_id": order.id})

    assert r.status_code == 403
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING


def test_flow_manual_match_nonexistent_scan_forbidden_for_stock_only():
    """Fail-closed: scan không tồn tại vẫn 403 với user chỉ có stock — nếu trả 400
    "không tìm thấy" thì mã trạng thái thành oracle dò scan_id nào tồn tại."""
    order = _minimal_order("gate0")
    client = _stock_only_client("stock-only-gate0")

    r = client.post("/api/inventory/flow-pending/999999/match", {"order_id": order.id})

    assert r.status_code == 403


def test_flow_manual_match_nonexistent_scan_returns_400_with_action_section():
    """User có section hành động: scan không tồn tại → 400 nghiệp vụ như cũ."""
    order = _minimal_order("gate0b")
    user = make_user_with_permissions(
        "shipments-gate0b",
        ["stock:view", "stock:edit", "shipments:view", "shipments:edit"],
    )
    client = APIClient()
    client.force_authenticate(user)

    r = client.post("/api/inventory/flow-pending/999999/match", {"order_id": order.id})

    assert r.status_code == 400


def test_flow_manual_match_dispatch_allowed_with_shipments_grandfathered():
    wh = Warehouse.objects.create(code="WH-GATE3", name="Kho gate3")
    p = Product.objects.create(sku="SKU-GATE3", name="SP gate3")
    order = _minimal_order("gate3")
    order.status = OrderStatus.AWAITING_SHIPMENT
    order.fulfillment_warehouse = wh
    order.save(update_fields=["status", "fulfillment_warehouse"])
    OrderItem.objects.create(
        order=order, product=p, product_name=p.name, sku=p.sku,
        quantity=1, unit_price=1000, line_total=1000,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=1,
        movement_type=MovementType.RECEIPT,
    )
    scan = PendingShipmentScan.objects.create(
        code="GATE-DISP-OK", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    # Role kho prod sau grandfather 0039: :view đi kèm :edit tương ứng.
    user = make_user_with_permissions(
        "stock-shipments-gate3",
        ["stock:view", "stock:edit", "shipments:view", "shipments:edit"],
    )
    client = APIClient()
    client.force_authenticate(user)

    r = client.post(f"/api/inventory/flow-pending/{scan.id}/match", {"order_id": order.id})

    assert r.status_code == 200, r.content
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED


def test_flow_manual_match_return_allowed_with_returns_grandfathered():
    wh = Warehouse.objects.create(code="WH-GATE4", name="Kho gate4")
    p = Product.objects.create(sku="SKU-GATE4", name="SP gate4")
    order = _minimal_order("gate4")
    OrderItem.objects.create(
        order=order, product=p, product_name=p.name, sku=p.sku,
        quantity=1, unit_price=1000, line_total=1000,
    )
    scan = PendingShipmentScan.objects.create(
        code="GATE-RET-OK", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    # returns:edit KHÔNG nằm trong grandfather 0039 (đợt 1 đã siết returns:create
    # riêng) — nhưng decorator là any-of nên stock:edit đủ để vào endpoint; gate
    # kind bên trong chỉ soi returns:view như cũ.
    user = make_user_with_permissions(
        "stock-returns-gate4", ["stock:view", "stock:edit", "returns:view"]
    )
    client = APIClient()
    client.force_authenticate(user)

    r = client.post(f"/api/inventory/flow-pending/{scan.id}/match", {"order_id": order.id})

    assert r.status_code == 200, r.content
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED


# ── Hàng gửi bù (đổi lỗi 2 chiều) qua khớp tay ───────────────────────────────


def test_flow_order_lookup_returns_dispatched_flag_and_items(client_admin):
    channel = Channel.objects.create(code="lp-lk", name="LK")
    customer = Customer.objects.create(
        full_name="K LK", phone_raw="0929222222", phone_normalized="0929222222"
    )
    warehouse = Warehouse.objects.create(name="Kho LK", code="KLK1")
    product = Product.objects.create(name="SP LK", sku="SKU-LK-1")
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach LK",
        phone="0929222222",
        total_amount="100000",
        status=OrderStatus.AWAITING_SHIPMENT,
        invoice_code="HD-LOOKUP-1",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order, product=product, product_name=product.name, sku=product.sku,
        quantity=3, unit_price=100000, line_total=300000,
    )
    # Đơn đã xuất kho: còn tồn NET đã trừ chưa hoàn.
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )

    res = client_admin.get("/api/inventory/flow-order-lookup?q=HD-LOOKUP-1")
    assert res.status_code == 200
    item = next(i for i in res.json()["items"] if i["id"] == order.id)
    assert item["already_dispatched"] is True
    assert item["items"] == [
        {"product_id": product.id, "sku": "SKU-LK-1", "quantity": 3}
    ]


def test_flow_order_lookup_no_sku_combo_thanh_ma_le(client_admin):
    """Tồn được trừ theo MÃ LẺ nên hạn mức phiếu hoàn cũng tính theo mã lẻ; gợi ý
    dòng combo ở đây sẽ khiến kho chọn đúng thứ UI đưa ra mà vẫn bị guard chặn.
    Xem docs/combo-stock-expansion-2026-08-20.md."""
    channel = Channel.objects.create(code="lp-cb", name="CB")
    customer = Customer.objects.create(
        full_name="K CB", phone_raw="0929333333", phone_normalized="0929333333"
    )
    warehouse = Warehouse.objects.create(name="Kho CB", code="KCB1")
    combo = Product.objects.create(name="Combo LK", sku="SKU-LK-COMBO")
    part_a = Product.objects.create(name="Phần A", sku="SKU-LK-A")
    part_b = Product.objects.create(name="Phần B", sku="SKU-LK-B")
    ProductComponent.objects.create(parent=combo, component=part_a, quantity=1)
    ProductComponent.objects.create(parent=combo, component=part_b, quantity=2)
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach CB",
        phone="0929333333",
        total_amount="100000",
        status=OrderStatus.AWAITING_SHIPMENT,
        invoice_code="HD-LOOKUP-CB",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order, product=combo, product_name=combo.name, sku=combo.sku,
        quantity=1, unit_price=100000, line_total=100000,
    )
    # Cùng đơn còn mua lẻ chính phần A → phải GỘP thành một dòng, khớp bút toán.
    OrderItem.objects.create(
        order=order, product=part_a, product_name=part_a.name, sku=part_a.sku,
        quantity=2, unit_price=50000, line_total=100000,
    )

    res = client_admin.get("/api/inventory/flow-order-lookup?q=HD-LOOKUP-CB")
    assert res.status_code == 200
    item = next(i for i in res.json()["items"] if i["id"] == order.id)
    assert item["items"] == [
        {"product_id": part_a.id, "sku": "SKU-LK-A", "quantity": 3},
        {"product_id": part_b.id, "sku": "SKU-LK-B", "quantity": 2},
    ]


def test_flow_order_lookup_combo_chua_khai_giu_nguyen(client_admin):
    """Fallback cùng luật với đường ghi sổ: chưa khai thành phần thì giữ SKU combo."""
    channel = Channel.objects.create(code="lp-cb2", name="CB2")
    customer = Customer.objects.create(
        full_name="K CB2", phone_raw="0929444444", phone_normalized="0929444444"
    )
    warehouse = Warehouse.objects.create(name="Kho CB2", code="KCB2")
    combo = Product.objects.create(name="Combo chưa khai", sku="SKU-LK-CHUAKHAI")
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach CB2",
        phone="0929444444",
        total_amount="100000",
        status=OrderStatus.AWAITING_SHIPMENT,
        invoice_code="HD-LOOKUP-CB2",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order, product=combo, product_name=combo.name, sku=combo.sku,
        quantity=1, unit_price=100000, line_total=100000,
    )

    res = client_admin.get("/api/inventory/flow-order-lookup?q=HD-LOOKUP-CB2")
    assert res.status_code == 200
    item = next(i for i in res.json()["items"] if i["id"] == order.id)
    assert item["items"] == [
        {"product_id": combo.id, "sku": "SKU-LK-CHUAKHAI", "quantity": 1}
    ]


def test_flow_manual_match_rejects_replacement_without_lines(client_admin):
    warehouse = Warehouse.objects.create(name="Kho RJ", code="KRJ1")
    scan = PendingShipmentScan.objects.create(
        code="VD-RJ-1", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    res = client_admin.post(
        f"/api/inventory/flow-pending/{scan.id}/match",
        data={"order_id": 1, "replacement": {"lines": [], "note": ""}},
        format="json",
    )
    assert res.status_code == 400
    assert "ít nhất 1 dòng" in res.json()["detail"]


def test_flow_manual_match_rejects_non_positive_quantity(client_admin):
    warehouse = Warehouse.objects.create(name="Kho RJ2", code="KRJ2")
    scan = PendingShipmentScan.objects.create(
        code="VD-RJ-2", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    res = client_admin.post(
        f"/api/inventory/flow-pending/{scan.id}/match",
        data={
            "order_id": 1,
            "replacement": {"lines": [{"product_id": 1, "quantity": 0}], "note": ""},
        },
        format="json",
    )
    assert res.status_code == 400
    assert "quantity" in res.json()["detail"]


# ─────────────────────── Bộ lọc NHIỀU kho (warehouse_ids) ───────────────────────


def _seed_second_warehouse(order_code: str = "DH002"):
    """Kho thứ hai với 1 đơn đã xuất + 1 mã chờ khớp riêng — để phân biệt được
    kết quả khi lọc 1 kho, 2 kho, hay không lọc kho nào."""
    channel = Channel.objects.get(code="lp-fh")
    customer = Customer.objects.get(phone_normalized="0900000001")
    wh2 = Warehouse.objects.create(code="WH-FH2", name="Kho B")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    order2 = Order.objects.create(
        channel=channel,
        customer=customer,
        name="K",
        phone="0900000001",
        order_code=order_code,
        invoice_code="HD002",
        shipping_code="VD002",
        total_amount="1000",
    )
    StockCommandService.record_movement(
        warehouse_id=wh2.id, product_id=p2.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order2.id,
    )
    PendingShipmentScan.objects.create(
        code="VDUNMATCHED2", kind=PendingScanKind.DISPATCH, warehouse=wh2,
        status=PendingScanStatus.PENDING,
    )
    return wh2, order2


def test_issue_orders_filters_by_multiple_warehouses(client_admin):
    wh, _order, _p1, _rn = _seed()
    wh2, _order2 = _seed_second_warehouse()

    both = client_admin.get(
        f"/api/inventory/dispatch-history/orders?limit=50&offset=0"
        f"&warehouse_ids={wh.id},{wh2.id}"
    ).json()
    assert {it["order_code"] for it in both["items"]} == {"DH001", "DH002"}
    assert both["total_quantity"] == 8

    only_first = client_admin.get(
        f"/api/inventory/dispatch-history/orders?limit=50&offset=0&warehouse_ids={wh.id}"
    ).json()
    assert [it["order_code"] for it in only_first["items"]] == ["DH001"]
    assert only_first["total_quantity"] == 5


def test_issue_orders_still_accepts_legacy_single_warehouse_id(client_admin):
    """Link đã lưu / tích hợp ngoài còn gửi `warehouse_id` đơn lẻ — vẫn phải lọc."""
    wh, _order, _p1, _rn = _seed()
    _seed_second_warehouse()
    body = client_admin.get(
        f"/api/inventory/dispatch-history/orders?limit=50&offset=0&warehouse_id={wh.id}"
    ).json()
    assert [it["order_code"] for it in body["items"]] == ["DH001"]


def test_flow_summary_and_pending_share_multi_warehouse_filter(client_admin):
    """Tile tổng hợp và khối mã chờ khớp phải cùng tập kho với bảng — lệch nhau
    thì màn hình tự mâu thuẫn (tile ra số của kho không được chọn)."""
    wh, _order, _p1, _rn = _seed()
    wh2, _order2 = _seed_second_warehouse()

    summary = client_admin.get(
        f"/api/inventory/dispatch-history/summary?warehouse_ids={wh.id},{wh2.id}"
    ).json()
    assert summary["issue"]["sale_quantity"] == 8

    pending = client_admin.get(
        f"/api/inventory/dispatch-history/pending?warehouse_ids={wh.id},{wh2.id}"
    ).json()["items"]
    assert {p["code"] for p in pending} == {"VDUNMATCHED", "VDUNMATCHED2"}

    pending_one = client_admin.get(
        f"/api/inventory/dispatch-history/pending?warehouse_ids={wh2.id}"
    ).json()["items"]
    assert {p["code"] for p in pending_one} == {"VDUNMATCHED2"}


def test_awaiting_return_tab_filters_by_multiple_warehouses(client_admin):
    """Tab 'Chờ hàng hoàn về' dùng CHUNG ô lọc kho nên cũng phải nhận nhiều kho."""
    wh, order, _p1, _rn = _seed()
    wh2, order2 = _seed_second_warehouse()
    wh3 = Warehouse.objects.create(code="WH-FH3", name="Kho C")
    now = timezone.now()
    CancelledDispatchWatch.objects.create(order=order, warehouse=wh, cancelled_at=now)
    CancelledDispatchWatch.objects.create(order=order2, warehouse=wh2, cancelled_at=now)

    body = client_admin.get(
        f"/api/inventory/dispatch-history/awaiting-return?limit=50&offset=0"
        f"&warehouse_ids={wh.id},{wh2.id}"
    ).json()
    assert {row["warehouse_id"] for row in body["results"]} == {wh.id, wh2.id}

    empty = client_admin.get(
        f"/api/inventory/dispatch-history/awaiting-return?limit=50&offset=0"
        f"&warehouse_ids={wh3.id}"
    ).json()
    assert empty["results"] == []


class TestParseWarehouseIds:
    """Luật đọc tham số kho dùng chung cho view và job export."""

    def test_parses_csv_and_drops_non_numeric(self):
        assert parse_warehouse_ids("3, 4,x", None) == [3, 4]

    def test_merges_legacy_single_param_without_duplicates(self):
        assert parse_warehouse_ids("3,4", "4") == [3, 4]
        assert parse_warehouse_ids("", "7") == [7]

    def test_empty_means_all_warehouses(self):
        assert parse_warehouse_ids(None, None) == []
        # id 0 không phải kho hợp lệ — bỏ, nếu không sẽ ra bộ lọc rỗng kết quả.
        assert parse_warehouse_ids("0", None) == []

    def test_accepts_list_from_export_job_params(self):
        assert parse_warehouse_ids([3, "4"], None) == [3, 4]
