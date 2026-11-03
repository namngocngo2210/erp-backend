"""Thao tác tay trên bảng mã chờ khớp: gỡ khớp, phiếu xuất bù, ghi chú.

Gộp từ test_pending_scan_unmatch / test_manual_match_replacement_issue /
test_pending_scan_note (đợt tái cấu trúc test, xem docs/test-restructure-2026-08-05.md).
"""
from __future__ import annotations

from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import (
    DocStatus,
    DocType,
    MovementReferenceType,
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnEntry,
    ReturnNote,
    ReturnNoteLine,
    ReturnStatus,
    ReturnType,
    StockDocument,
    StockMovement,
)
from apps.inventory.tests.helpers import pending_scan as _scan
from apps.inventory.tests.helpers import sellable as _sellable
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


# ─────────────────────── Gỡ khớp (unmatch_dispatch) ───────────────────────
# Gỡ khớp mã XUẤT đã khớp nhầm đơn (unmatch_dispatch).
#
# Nghiệp vụ: kho quét mã / khớp tay nhầm đơn → phải có đường lùi ngay tại bảng chờ:
# hoàn phần tồn lần khớp đó đã trừ, trả trạng thái giao vận của đơn về "Kho đang xử
# lý", đưa mã về FAILED để khớp lại đúng đơn (cron chỉ quét PENDING nên không tự
# khớp lại mã vừa gỡ).
#
# Guard quan trọng nhất: KHÔNG hoàn tồn cho lần khớp không ghi SALE
# (`already_dispatched` — đơn đã xuất từ trước, bút toán không phải của lần khớp này).

DISPATCHED = "dispatched"
WAREHOUSE_PROCESSING = "warehouse_processing"


def _make_order(channel, warehouse, *, code: str, shipping_code: str) -> Order:
    customer = Customer.objects.create(
        full_name=f"Khach {code}",
        phone_raw=f"09{abs(hash(code)) % 10**8:08d}",
        phone_normalized=f"09{abs(hash(code)) % 10**8:08d}",
        code=f"CUST-{code}",
    )
    product = Product.objects.create(name=f"SP {code}", sku=f"SKU-{code}")
    o = Order.objects.create(
        channel=channel,
        customer=customer,
        name=customer.full_name,
        phone=customer.phone_raw,
        total_amount="200000",
        status=OrderStatus.AWAITING_SHIPMENT,
        shipping_code=shipping_code,
        invoice_code=f"HD-{code}",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=o,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=1,
        unit_price=100000,
        line_total=100000,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=5,
        movement_type=MovementType.RECEIPT,
    )
    return o


def _match(scan, order, actor):
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor
    )
    assert result["ok"] is True
    return result


def _delivery_code(order) -> str:
    order.refresh_from_db()
    return order.assessment.delivery_business_status_code


def test_unmatch_releases_stock_and_resets_order_and_scan(channel, warehouse, actor):
    order = _make_order(channel, warehouse, code="A1", shipping_code="VD-A1")
    product_id = order.items.first().product_id
    scan = _scan(warehouse, actor, "VD-A1")
    _match(scan, order, actor)
    assert _sellable(warehouse.id, product_id) == 4
    assert _delivery_code(order) == DISPATCHED

    result = PendingScanService.unmatch_dispatch(scan_id=scan.id, actor=actor)

    assert result["ok"] is True
    assert result["released_stock"] is True
    assert _sellable(warehouse.id, product_id) == 5
    assert _delivery_code(order) == WAREHOUSE_PROCESSING
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.FAILED
    assert "gỡ khớp" in scan.error.lower()


def test_rematch_to_correct_order_after_unmatch(channel, warehouse, actor):
    """Cùng mã VĐ có 2 đơn — khớp nhầm đơn 1, gỡ, khớp lại đơn 2 phải chạy trọn."""
    wrong = _make_order(channel, warehouse, code="B1", shipping_code="VD-B")
    right = _make_order(channel, warehouse, code="B2", shipping_code="VD-B")
    wrong_pid = wrong.items.first().product_id
    right_pid = right.items.first().product_id
    scan = _scan(warehouse, actor, "VD-B")
    _match(scan, wrong, actor)

    assert PendingScanService.unmatch_dispatch(scan_id=scan.id, actor=actor)["ok"]
    rematch = PendingScanService.manual_match(
        scan_id=scan.id, order_id=right.id, actor=actor
    )

    assert rematch["ok"] is True
    assert _sellable(warehouse.id, wrong_pid) == 5
    assert _sellable(warehouse.id, right_pid) == 4
    assert _delivery_code(right) == DISPATCHED
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.matched_order_id == right.id


def test_unmatch_already_dispatched_scan_does_not_release(channel, warehouse, actor):
    """Lần khớp bị `already_dispatched` không ghi SALE → gỡ chỉ tháo scan, giữ nguyên tồn + đơn."""
    order = _make_order(channel, warehouse, code="C1", shipping_code="VD-C1")
    product_id = order.items.first().product_id
    first = _scan(warehouse, actor, "VD-C1")
    _match(first, order, actor)
    second = _scan(warehouse, actor, "VD-C1-DOI")
    _match(second, order, actor)  # đơn đã xuất → warning, không trừ thêm
    assert _sellable(warehouse.id, product_id) == 4

    result = PendingScanService.unmatch_dispatch(scan_id=second.id, actor=actor)

    assert result["ok"] is True
    assert result["released_stock"] is False
    assert _sellable(warehouse.id, product_id) == 4
    assert _delivery_code(order) == DISPATCHED
    second.refresh_from_db()
    assert second.status == PendingScanStatus.FAILED


def test_unmatch_blocked_when_replacement_document_exists(channel, warehouse, actor):
    order = _make_order(channel, warehouse, code="D1", shipping_code="VD-D1")
    product_id = order.items.first().product_id
    _match(_scan(warehouse, actor, "VD-D1"), order, actor)
    scan = _scan(warehouse, actor, "VD-D1-DOI")
    result = PendingScanService.manual_match(
        scan_id=scan.id,
        order_id=order.id,
        actor=actor,
        replacement={"lines": [{"product_id": product_id, "quantity": 1}], "note": ""},
    )
    assert result["stock_doc_code"]

    blocked = PendingScanService.unmatch_dispatch(scan_id=scan.id, actor=actor)

    assert blocked["ok"] is False
    assert "phiếu" in blocked["error"]
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED


def test_unmatch_blocked_when_order_moved_past_dispatched(channel, warehouse, actor):
    """Đơn đã sang trạng thái sau xuất (vd Đã nhận) → không tự ý lùi, báo xử lý trên đơn."""
    from apps.orders.application.services.order_service_impl import OrderService

    order = _make_order(channel, warehouse, code="E1", shipping_code="VD-E1")
    scan = _scan(warehouse, actor, "VD-E1")
    _match(scan, order, actor)
    order.refresh_from_db()
    OrderService.update_order_inline(
        order, {"crm": {"delivery_business_status_code": "received"}}, actor
    )

    blocked = PendingScanService.unmatch_dispatch(scan_id=scan.id, actor=actor)

    assert blocked["ok"] is False
    assert "Đã xuất kho" in blocked["error"]
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED


def test_unmatch_rejects_unmatched_or_missing_scan(channel, warehouse, actor):
    order = _make_order(channel, warehouse, code="F1", shipping_code="VD-F1")
    pending = _scan(warehouse, actor, "VD-F1-PENDING")

    assert PendingScanService.unmatch_dispatch(scan_id=pending.id, actor=actor)["ok"] is False
    assert PendingScanService.unmatch_dispatch(scan_id=10**9, actor=actor)["ok"] is False
    assert order.id  # giữ fixture cho rõ nghĩa: chưa hề khớp gì vào đơn


def test_unmatch_rejects_warranty_matched_scan(warehouse, actor):
    scan = PendingShipmentScan.objects.create(
        code="VD-BH-1",
        kind=PendingScanKind.DISPATCH,
        warehouse=warehouse,
        scanned_by=actor,
        status=PendingScanStatus.MATCHED,
        result_ref="warranty-journey:1",
    )
    result = PendingScanService.unmatch_dispatch(scan_id=scan.id, actor=actor)
    assert result["ok"] is False


def test_unmatch_endpoint_happy_path_and_permissions(channel, warehouse, actor):
    """POST /flow-pending/{id}/unmatch: admin gỡ được; user chỉ có section stock bị 403
    (hành động thực sự là hoàn tồn xuất kho → soi section shipments như luồng quét)."""
    from rest_framework.test import APIClient

    from apps.users.tests.helpers import make_user_with_sections

    order = _make_order(channel, warehouse, code="H1", shipping_code="VD-H1")
    product_id = order.items.first().product_id
    scan = _scan(warehouse, actor, "VD-H1")
    _match(scan, order, actor)

    stock_only = APIClient()
    stock_only.force_authenticate(make_user_with_sections("unmatch-stock-only", ["stock"]))
    denied = stock_only.post(f"/api/inventory/flow-pending/{scan.id}/unmatch")
    assert denied.status_code == 403

    admin = APIClient()
    admin.force_authenticate(actor)
    ok = admin.post(f"/api/inventory/flow-pending/{scan.id}/unmatch")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert _sellable(warehouse.id, product_id) == 5

    again = admin.post(f"/api/inventory/flow-pending/{scan.id}/unmatch")
    assert again.status_code == 400


def test_list_unmatched_flags_can_unmatch(channel, warehouse, actor):
    """FE cần cờ `can_unmatch` để chỉ hiện nút Gỡ khớp cho dòng gỡ được."""
    order = _make_order(channel, warehouse, code="G1", shipping_code="VD-G1")
    matched = _scan(warehouse, actor, "VD-G1")
    _match(matched, order, actor)
    pending = _scan(warehouse, actor, "VD-G1-PENDING")

    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.DISPATCH, match_filter="all"
    )
    by_id = {i["id"]: i for i in items}
    assert by_id[matched.id]["can_unmatch"] is True
    assert by_id[pending.id]["can_unmatch"] is False


# ─────────────────── Kho ghi sổ khi khớp tay (manual_match) ───────────────────
# Review whole-branch 29/08/2026: manual_match là đường XỬ LÝ TAY chuẩn của cron
# khi cron khớp trượt một mã HOÁ ĐƠN (đơn bán trực tiếp) — panel này chỉ là cách
# gọi khác của cùng một luật chọn kho, không phải một đường riêng. Trước bản vá,
# nó luôn trừ ở `scan.warehouse_id` (kho màn quét) bất kể đường khớp, âm thầm
# trừ sai kho cho ca khớp theo mã hoá đơn.


class TestManualMatchWarehouseRouting:
    def test_invoice_code_deducts_at_order_warehouse_not_scan_warehouse(
        self, channel, actor
    ):
        order_wh = Warehouse.objects.create(name="Kho don INV", code="KT-INV-ORD")
        scan_wh = Warehouse.objects.create(name="Kho quet tay INV", code="KT-INV-SCAN")
        order = _make_order(channel, order_wh, code="INV1", shipping_code="")
        product_id = order.items.first().product_id

        scan = PendingShipmentScan.objects.create(
            code=order.invoice_code,
            kind=PendingScanKind.DISPATCH,
            warehouse=scan_wh,
            scanned_by=actor,
        )

        result = PendingScanService.manual_match(
            scan_id=scan.id, order_id=order.id, actor=actor
        )

        assert result["ok"] is True
        assert _sellable(order_wh.id, product_id) == 4
        assert _sellable(scan_wh.id, product_id) == 0
        scan.refresh_from_db()
        assert scan.status == PendingScanStatus.MATCHED

    def test_invoice_code_without_order_warehouse_is_refused(self, channel, actor):
        order_wh = Warehouse.objects.create(name="Kho don INV2", code="KT-INV-ORD2")
        scan_wh = Warehouse.objects.create(name="Kho quet tay INV2", code="KT-INV-SCAN2")
        order = _make_order(channel, order_wh, code="INV2", shipping_code="")
        order.fulfillment_warehouse = None
        order.save(update_fields=["fulfillment_warehouse"])
        product_id = order.items.first().product_id

        scan = PendingShipmentScan.objects.create(
            code=order.invoice_code,
            kind=PendingScanKind.DISPATCH,
            warehouse=scan_wh,
            scanned_by=actor,
        )

        result = PendingScanService.manual_match(
            scan_id=scan.id, order_id=order.id, actor=actor
        )

        assert result["ok"] is False
        assert "kho xuất" in result["error"].lower()
        scan.refresh_from_db()
        assert scan.status == PendingScanStatus.FAILED
        # Fail loudly TRƯỚC khi trừ tồn — không có bút toán nào của lần khớp này.
        assert not StockMovement.objects.filter(
            reference_type="order", reference_id=order.id
        ).exists()
        assert _sellable(order_wh.id, product_id) == 5
        assert _sellable(scan_wh.id, product_id) == 0

    def test_shipping_code_manual_match_still_uses_scan_warehouse(self, channel, actor):
        """Khoá hành vi cũ: khớp tay theo MÃ VẬN ĐƠN vẫn trừ ở kho màn quét, không
        phải kho của đơn — hành vi này KHÔNG được đổi bởi bản vá nhánh mã hoá đơn."""
        order_wh = Warehouse.objects.create(name="Kho don SHIP", code="KT-SHIP-ORD")
        scan_wh = Warehouse.objects.create(name="Kho quet tay SHIP", code="KT-SHIP-SCAN")
        order = _make_order(channel, order_wh, code="SHIP1", shipping_code="VD-SHIP1")
        product_id = order.items.first().product_id
        StockCommandService.record_movement(
            warehouse_id=scan_wh.id, product_id=product_id, qty_delta=5,
            movement_type=MovementType.RECEIPT,
        )

        scan = PendingShipmentScan.objects.create(
            code="VD-SHIP1",
            kind=PendingScanKind.DISPATCH,
            warehouse=scan_wh,
            scanned_by=actor,
        )

        result = PendingScanService.manual_match(
            scan_id=scan.id, order_id=order.id, actor=actor
        )

        assert result["ok"] is True
        assert _sellable(scan_wh.id, product_id) == 4
        assert _sellable(order_wh.id, product_id) == 5


    # Đơn sàn giao HOẢ TỐC (Shopee → Grab/Ahamove): tem in MÃ ĐƠN SÀN
    # ("260901TYHC0T2V"), kho quét mã đó. Đơn trong hệ thống mang shipping_code là
    # mã Grab ("205544542") và invoice_code là "HDSPE_<mã đơn sàn>" — mã quét KHÔNG
    # bằng cái nào cả, cron không tự khớp, kho phải khớp tay. Trước 29/08 khớp tay
    # trừ ở kho màn quét như mọi đơn giao vận; bản vá 36f78f4d coi "không phải mã
    # vận đơn" = "khớp mã hoá đơn" → đòi kho của đơn (đơn sàn không có) → FAILED
    # "Đơn thiếu kho xuất" — hồi quy trên prod 01/09/2026 (scan #1508/#1527/#1532).

    def test_marketplace_order_code_without_order_warehouse_uses_scan_warehouse(
        self, channel, actor
    ):
        scan_wh = Warehouse.objects.create(name="Kho quet HT", code="KT-HT-SCAN")
        order = _make_order(channel, scan_wh, code="HT1", shipping_code="205544542")
        order.invoice_code = "HDSPE_260901TYHC0T2V"
        order.fulfillment_warehouse = None
        order.save(update_fields=["invoice_code", "fulfillment_warehouse"])
        product_id = order.items.first().product_id

        scan = PendingShipmentScan.objects.create(
            code="260901TYHC0T2V",
            kind=PendingScanKind.DISPATCH,
            warehouse=scan_wh,
            scanned_by=actor,
        )

        result = PendingScanService.manual_match(
            scan_id=scan.id, order_id=order.id, actor=actor
        )

        assert result["ok"] is True, result
        assert _sellable(scan_wh.id, product_id) == 4
        scan.refresh_from_db()
        assert scan.status == PendingScanStatus.MATCHED
        assert scan.matched_order_id == order.id

    def test_marketplace_order_code_with_order_warehouse_still_uses_scan_warehouse(
        self, channel, actor
    ):
        """Đơn giao vận khớp tay theo mã đơn sàn là đường MÃ VẬN ĐƠN về nghiệp vụ
        (kho đang cầm gói hàng), không phải đường mã hoá đơn — kho của đơn (nếu có)
        không được lặng lẽ thắng kho màn quét."""
        order_wh = Warehouse.objects.create(name="Kho don HT2", code="KT-HT2-ORD")
        scan_wh = Warehouse.objects.create(name="Kho quet HT2", code="KT-HT2-SCAN")
        order = _make_order(channel, order_wh, code="HT2", shipping_code="VN2633780249470")
        order.invoice_code = "HDSPE_260901T4N29F1A"
        order.save(update_fields=["invoice_code"])
        product_id = order.items.first().product_id
        StockCommandService.record_movement(
            warehouse_id=scan_wh.id, product_id=product_id, qty_delta=5,
            movement_type=MovementType.RECEIPT,
        )

        scan = PendingShipmentScan.objects.create(
            code="260901T4N29F1A",
            kind=PendingScanKind.DISPATCH,
            warehouse=scan_wh,
            scanned_by=actor,
        )

        result = PendingScanService.manual_match(
            scan_id=scan.id, order_id=order.id, actor=actor
        )

        assert result["ok"] is True, result
        assert _sellable(scan_wh.id, product_id) == 4
        assert _sellable(order_wh.id, product_id) == 5


# ─────────────────────── Phiếu xuất bù (replacement issue) ───────────────────────
# Hàng gửi bù (đổi lỗi 2 chiều): khớp tay vào đơn ĐÃ xuất → sinh Phiếu Xuất kho chờ duyệt.
#
# Guard quan trọng nhất là chống TRỪ TỒN 2 LẦN: chỉ tạo phiếu khi lần khớp đó thực sự
# bị `already_dispatched` chặn, tức là không có bút toán SALE nào vừa được ghi.


@pytest.fixture
def order(make_order, channel, warehouse):
    """Đơn AWAITING_SHIPMENT, 1 SKU × 1, tồn kho đủ 5 cái."""
    product = Product.objects.create(name="SP Bu", sku="MSC06-T")
    return make_order(
        channel=channel, warehouse=warehouse, product=product, quantity=1, stock_qty=5,
        name="Khach Bu", phone="0929111111", customer_code="CUST-BU-1",
        shipping_code="VD-GOC", invoice_code="HD-TEST-1",
    )


def _product_id(order) -> int:
    return order.items.first().product_id


def _dispatch_first_time(order, warehouse, actor):
    """Lần xuất gốc: khớp tay mã VĐ gốc → ghi SALE, đơn có tồn treo."""
    scan = _scan(warehouse, actor, "VD-GOC")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor
    )
    assert result["ok"] is True
    return result


def _replacement(product_id: int, qty: int = 1, note: str = "") -> dict:
    return {"lines": [{"product_id": product_id, "quantity": qty}], "note": note}


def test_creates_pending_approval_issue_document(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    after_first = _sellable(warehouse.id, product_id)

    scan = _scan(warehouse, actor, "VD-DOI-2CHIEU")
    result = PendingScanService.manual_match(
        scan_id=scan.id,
        order_id=order.id,
        actor=actor,
        replacement=_replacement(product_id, note="ko vao dien"),
    )

    assert result["ok"] is True
    assert result["replacement_skipped"] == ""
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    assert doc.doc_type == DocType.ISSUE
    assert doc.status == DocStatus.PENDING_APPROVAL
    assert doc.source_warehouse_id == warehouse.id
    # Mốc nghiệp vụ = lúc kho QUÉT, không phải lúc khớp/duyệt.
    assert doc.occurred_at == scan.created_at
    # Note mang mã đơn + mã VĐ để đối soát ngược, kèm ghi chú người dùng.
    assert "HD-TEST-1" in doc.note
    assert "VD-DOI-2CHIEU" in doc.note
    assert "ko vao dien" in doc.note
    # Chưa duyệt → tồn CHƯA đổi.
    assert _sellable(warehouse.id, product_id) == after_first

    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.result_ref == f"stock-doc:{doc.id}"


def test_approving_document_deducts_stock_once(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    after_first = _sellable(warehouse.id, product_id)

    scan = _scan(warehouse, actor, "VD-DOI-2")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    StockDocumentService.approve_document(document_id=doc.id, user_id=actor.id)

    assert after_first - _sellable(warehouse.id, product_id) == 1
    movement = StockMovement.objects.get(
        reference_type="document", reference_id=doc.id, movement_type=MovementType.ISSUE
    )
    assert movement.occurred_at == scan.created_at


def test_second_call_does_not_create_duplicate_document(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-3")
    PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    # Khớp lại cùng scan (double-click / retry): scan đã MATCHED nên bị chặn từ đầu.
    second = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    assert second["ok"] is False
    assert StockDocument.objects.filter(doc_type=DocType.ISSUE).count() == 1


def test_not_dispatched_order_skips_document(order, warehouse, actor):
    """Đơn CHƯA từng xuất: lần khớp này ghi SALE → tuyệt đối không tạo thêm phiếu."""
    product_id = _product_id(order)
    scan = _scan(warehouse, actor, "VD-GOC")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    assert result["ok"] is True
    assert result["stock_doc_code"] == ""
    assert result["replacement_skipped"] == "order_was_not_dispatched"
    assert StockDocument.objects.count() == 0
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    ).exists()


def test_invalid_lines_skip_document(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-4")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement={"lines": [{"product_id": product_id, "quantity": 0}], "note": ""},
    )
    assert result["ok"] is True
    assert result["replacement_skipped"] == "invalid_lines"
    assert StockDocument.objects.count() == 0


def test_document_appears_in_issue_report_on_scan_date(order, warehouse, actor):
    """Phiếu bù phải rơi đúng NGÀY QUÉT trong báo cáo Xuất; huỷ phiếu → net về 0."""
    from django.utils import timezone

    from apps.inventory.application.services.issue_document_history import (
        MergedIssueHistory,
    )

    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-6")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    StockDocumentService.approve_document(document_id=doc.id, user_id=actor.id)

    day = timezone.localtime(scan.created_at).date().isoformat()
    page = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, date_from=day, date_to=day
    )
    assert page["doc_quantity"] == 1
    assert any(
        item.get("kind") == "document" and item.get("doc_code") == doc.code
        for item in page["items"]
    )

    StockDocumentService.cancel_document(document_id=doc.id, user_id=actor.id)
    after = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, date_from=day, date_to=day
    )
    assert after["doc_quantity"] == 0


def test_report_search_finds_document_by_order_and_tracking_code(order, warehouse, actor):
    """Ô tìm khớp doc.note → gõ mã đơn gốc HOẶC mã VĐ đều phải ra dòng phiếu bù."""
    from django.utils import timezone

    from apps.inventory.application.services.issue_document_history import (
        MergedIssueHistory,
    )

    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-7")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    StockDocumentService.approve_document(document_id=doc.id, user_id=actor.id)

    day = timezone.localtime(scan.created_at).date().isoformat()
    for term in ("HD-TEST-1", "VD-DOI-7"):
        page = MergedIssueHistory.orders_page(
            pagination={"limit": 50, "offset": 0},
            date_from=day, date_to=day, order=term,
        )
        codes = [i.get("doc_code") for i in page["items"] if i.get("kind") == "document"]
        assert doc.code in codes, f"tìm '{term}' không ra dòng phiếu bù"


def test_historical_scan_is_flagged_and_can_create_document(order, warehouse, actor):
    """Ca CŨ: đã khớp vào đơn đã xuất, chưa có phiếu bù → phải bị đánh dấu và xử lý được."""
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-CU-1")
    # Khớp KHÔNG kèm replacement = đúng hành vi trước khi có tính năng này.
    PendingScanService.manual_match(scan_id=scan.id, order_id=order.id, actor=actor)
    scan.refresh_from_db()
    assert PendingScanService.scan_needs_replacement_issue(scan) is True

    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.DISPATCH, match_filter="matched"
    )
    flagged = next(i for i in items if i["id"] == scan.id)
    assert flagged["needs_replacement_issue"] is True

    result = PendingScanService.create_replacement_issue_for_scan(
        scan_id=scan.id,
        replacement={"lines": [{"product_id": product_id, "quantity": 1}], "note": ""},
        actor=actor,
    )
    assert result["ok"] is True
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    assert doc.status == DocStatus.PENDING_APPROVAL
    assert doc.occurred_at == scan.created_at

    # Đã xử lý → hết bị đánh dấu, và không tạo được phiếu thứ 2.
    scan.refresh_from_db()
    assert PendingScanService.scan_needs_replacement_issue(scan) is False
    again = PendingScanService.create_replacement_issue_for_scan(
        scan_id=scan.id,
        replacement={"lines": [{"product_id": product_id, "quantity": 1}], "note": ""},
        actor=actor,
    )
    assert again["ok"] is False
    assert StockDocument.objects.count() == 1


def test_scan_matched_to_undispatched_order_is_not_flagged(order, warehouse, actor):
    """Khớp vào đơn CHƯA xuất (ghi SALE bình thường) → không được coi là cần phiếu bù."""
    scan = _scan(warehouse, actor, "VD-GOC")
    PendingScanService.manual_match(scan_id=scan.id, order_id=order.id, actor=actor)
    scan.refresh_from_db()
    assert PendingScanService.scan_needs_replacement_issue(scan) is False
    result = PendingScanService.create_replacement_issue_for_scan(
        scan_id=scan.id,
        replacement={"lines": [{"product_id": _product_id(order), "quantity": 1}], "note": ""},
        actor=actor,
    )
    assert result["ok"] is False
    assert StockDocument.objects.count() == 0


def test_without_replacement_behaviour_unchanged(order, warehouse, actor):
    """Đường cũ (replacement=None) không được đổi một chút nào."""
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-5")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor
    )
    assert result["ok"] is True
    assert result["stock_doc_code"] == ""
    assert result["replacement_skipped"] == ""
    assert StockDocument.objects.count() == 0


# ─────────────────────── Ghi chú (PendingShipmentScan.note) ───────────────────────
# Ghi chú trên dòng mã chờ khớp (PendingShipmentScan.note).
#
# Cho phép nhân viên kho ghi chú ngay khi mã còn CHỜ KHỚP (chưa gắn được đơn),
# sửa qua PATCH /api/inventory/flow-pending/<scan_id>/note, và ghi chú được
# kế thừa sang phiếu trả khi mã khớp thành công (kind=return).


@pytest.fixture
def client_admin():
    c = APIClient()
    c.force_authenticate(make_admin("admin-pending-note"))
    return c


def _order_with_item(warehouse, shipping_code="VDNOTE", qty=2):
    channel = Channel.objects.create(code="lp-note", name="Note Channel")
    customer = Customer.objects.create(
        full_name="Khach Note", phone_raw="0900000123", phone_normalized="0900000123"
    )
    product = Product.objects.create(name="SP Note", sku="SKU-NOTE")
    order = Order.objects.create(
        channel=channel, customer=customer, name="Khach Note", phone="0900000123",
        total_amount="200000", status=OrderStatus.AWAITING_SHIPMENT,
        shipping_code=shipping_code, fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order, product=product, product_name=product.name, sku=product.sku,
        quantity=qty, unit_price=100000, line_total=100000 * qty,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=qty,
        movement_type=MovementType.RECEIPT,
    )
    return order


# ─────────────────────── Service ───────────────────────


def test_update_note_sets_note_on_pending_scan(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="VD1", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.PENDING,
    )
    updated = PendingScanService.update_note(scan_id=scan.id, note="khách hẹn giao lại")
    assert updated is not None
    scan.refresh_from_db()
    assert scan.note == "khách hẹn giao lại"


def test_update_note_missing_scan_returns_none(warehouse):
    assert PendingScanService.update_note(scan_id=999999, note="x") is None


def test_update_note_empty_clears(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="VD2", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.PENDING, note="cũ",
    )
    PendingScanService.update_note(scan_id=scan.id, note="")
    scan.refresh_from_db()
    assert scan.note == ""


def test_list_unmatched_exposes_scan_note(warehouse):
    PendingShipmentScan.objects.create(
        code="VD3", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.PENDING, note="ghi chú chờ khớp",
    )
    items = PendingScanService.list_unmatched(kind=PendingScanKind.DISPATCH)
    assert items[0]["note"] == "ghi chú chờ khớp"
    # Bản ghi không có người quét → chuỗi rỗng, không lỗi.
    assert items[0]["performed_by"] == ""


def test_list_unmatched_exposes_scanner_name(warehouse):
    scanner = make_admin("scanner-pending-note")
    PendingShipmentScan.objects.create(
        code="VD3B", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.PENDING, scanned_by=scanner,
    )
    items = PendingScanService.list_unmatched(kind=PendingScanKind.DISPATCH)
    by_code = {it["code"]: it for it in items}
    assert by_code["VD3B"]["performed_by"] == (
        scanner.display_name or scanner.username
    )


# ─────────────────────── SKU lines (dòng đã khớp) ───────────────────────


def _order_no_item(warehouse, order_code="DH-PSKU"):
    channel = Channel.objects.create(code=f"lp-{order_code}", name="PSKU Channel")
    customer = Customer.objects.create(
        full_name="Khach PSKU", phone_raw="0900000456", phone_normalized="0900000456"
    )
    return Order.objects.create(
        channel=channel, customer=customer, name="Khach PSKU", phone="0900000456",
        total_amount="200000", status=OrderStatus.AWAITING_SHIPMENT,
        order_code=order_code, fulfillment_warehouse=warehouse,
    )


def test_list_unmatched_dispatch_matched_row_has_sku_lines(warehouse):
    """Mã quét XUẤT đã khớp → lines lấy từ StockMovement của đơn (net = xuất − hoàn)."""
    order = _order_no_item(warehouse, order_code="DH-PSKU1")
    p1 = Product.objects.create(name="SP1 PSKU", sku="SKU1")
    p2 = Product.objects.create(name="SP2 PSKU", sku="SKU2")
    # SKU1: xuất 3 + hoàn 1 → net 2
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=p1.id, qty_delta=-3,
        movement_type=MovementType.SALE,
        reference_type=MovementReferenceType.ORDER, reference_id=order.id,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=p1.id, qty_delta=1,
        movement_type=MovementType.SALE_REVERSAL,
        reference_type=MovementReferenceType.ORDER, reference_id=order.id,
    )
    # SKU2: xuất 5, không hoàn → net 5
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=p2.id, qty_delta=-5,
        movement_type=MovementType.SALE,
        reference_type=MovementReferenceType.ORDER, reference_id=order.id,
    )
    PendingShipmentScan.objects.create(
        code="VDDISP1", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED, matched_order=order,
    )
    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.DISPATCH, match_filter="matched"
    )
    row = items[0]
    assert [(ln["sku"], ln["qty"]) for ln in row["lines"]] == [("SKU2", 5), ("SKU1", 2)]
    assert row["sku_count"] == 2
    assert row["qty"] == 7


def test_list_unmatched_pending_row_has_no_lines(warehouse):
    """Mã CHƯA khớp thì chưa có đơn → lines rỗng."""
    PendingShipmentScan.objects.create(
        code="VDDISP2", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.PENDING,
    )
    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.DISPATCH, match_filter="unmatched"
    )
    row = items[0]
    assert row["lines"] == []
    assert row["sku_count"] == 0
    assert row["qty"] == 0


def test_list_unmatched_dispatch_failed_row_has_no_sku_lines(warehouse):
    """Mã FAILED cũng có matched_order gắn (guard chặn xuất trùng) → KHÔNG được
    lộ SKU/SL của đơn đó, kẻo trông y hệt dòng đã xuất kho thành công."""
    order = _order_no_item(warehouse, order_code="DH-PSKU-FAIL")
    product = Product.objects.create(name="SP FAIL", sku="SKU-FAIL")
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-2,
        movement_type=MovementType.SALE,
        reference_type=MovementReferenceType.ORDER, reference_id=order.id,
    )
    PendingShipmentScan.objects.create(
        code="VDDISPFAIL", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.FAILED, matched_order=order,
        error="đã xuất kho trước đó",
    )
    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.DISPATCH, match_filter="unmatched"
    )
    row = items[0]
    assert row["lines"] == []
    assert row["sku_count"] == 0
    assert row["qty"] == 0


def test_list_unmatched_return_matched_row_has_sku_lines(warehouse):
    """Mã quét HOÀN đã khớp → lines lấy từ dòng của NHÓM ĐƠN (ReturnEntry).

    Phiếu ngày gom nhiều đơn: nhóm đơn khác trong CÙNG phiếu không được lọt vào
    lines của dòng mã quét này.
    """
    p1 = Product.objects.create(name="SP1 PSKU RET", sku="SKU1")
    p2 = Product.objects.create(name="SP2 PSKU RET", sku="SKU2")
    p3 = Product.objects.create(name="SP3 PSKU RET", sku="SKU3")
    note = ReturnNote.objects.create(
        code="PTH-20260105-K1", warehouse=warehouse, doc_date=date(2026, 1, 5)
    )
    entry = ReturnEntry.objects.create(
        return_note=note, shipping_code="VDRET1", status=ReturnStatus.POSTED
    )
    ReturnNoteLine.objects.create(
        entry=entry, product=p1, quantity=2, return_type=ReturnType.DELIVERY_RETURNED
    )
    ReturnNoteLine.objects.create(
        entry=entry, product=p2, quantity=1, return_type=ReturnType.DELIVERY_RETURNED
    )
    # Nhóm đơn khác cùng phiếu ngày — KHÔNG được xuất hiện trong lines dòng trên.
    other = ReturnEntry.objects.create(
        return_note=note, shipping_code="VDRET-OTHER", status=ReturnStatus.POSTED
    )
    ReturnNoteLine.objects.create(
        entry=other, product=p3, quantity=9, return_type=ReturnType.DELIVERY_RETURNED
    )
    PendingShipmentScan.objects.create(
        code="VDRET1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.RETURN, match_filter="matched", with_return_note=True
    )
    row = next(it for it in items if it["code"] == "VDRET1")
    assert [(ln["sku"], ln["qty"]) for ln in row["lines"]] == [("SKU1", 2), ("SKU2", 1)]
    assert row["sku_count"] == 2
    assert row["qty"] == 3


# ─────────────────────── Carry-over khi khớp ───────────────────────


def test_return_scan_note_carried_to_return_entry(warehouse, client_admin):
    order = _order_with_item(warehouse, shipping_code="VDRETNOTE")
    scan = PendingShipmentScan.objects.create(
        code="VDRETNOTE", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", status=PendingScanStatus.PENDING,
        note="khách báo vỡ hàng",
    )
    PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    entry = ReturnEntry.objects.get(shipping_code="VDRETNOTE")
    assert entry.note == "khách báo vỡ hàng"
    assert entry.source_order_id == order.id


def test_list_unmatched_attaches_entry_id_and_note(warehouse):
    # Dòng đã khớp ở tab Nhập hoàn: return_id mang ID NHÓM ĐƠN (ReturnEntry) —
    # FE dùng nó để sửa ghi chú (PATCH /return-history/{id}/note) và huỷ đơn hoàn.
    _order_with_item(warehouse, shipping_code="VDLIST")
    PendingShipmentScan.objects.create(
        code="VDLIST", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", status=PendingScanStatus.PENDING,
        note="ghi chú kho",
    )
    PendingScanService.match_pending_scans()
    entry = ReturnEntry.objects.get(shipping_code="VDLIST")
    items = PendingScanService.list_unmatched(
        kind=PendingScanKind.RETURN, match_filter="matched", with_return_note=True
    )
    row = next(it for it in items if it["code"] == "VDLIST")
    assert row["return_id"] == entry.id
    assert row["note"] == entry.note


# ─────────────────────── Endpoint ───────────────────────


def test_patch_pending_note_endpoint(client_admin, warehouse):
    scan = PendingShipmentScan.objects.create(
        code="VD4", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.PENDING,
    )
    r = client_admin.patch(
        f"/api/inventory/flow-pending/{scan.id}/note",
        {"note": "kho ghi chú"}, format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json() == {"id": scan.id, "note": "kho ghi chú"}
    scan.refresh_from_db()
    assert scan.note == "kho ghi chú"


def test_patch_pending_note_not_found(client_admin):
    r = client_admin.patch(
        "/api/inventory/flow-pending/999999/note", {"note": "x"}, format="json"
    )
    assert r.status_code == 404


def test_patch_pending_note_rejects_non_string(client_admin, warehouse):
    scan = PendingShipmentScan.objects.create(
        code="VD5", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.PENDING,
    )
    r = client_admin.patch(
        f"/api/inventory/flow-pending/{scan.id}/note", {"note": 123}, format="json"
    )
    assert r.status_code == 400
