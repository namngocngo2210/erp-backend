"""Lõi nhóm đơn hoàn (ReturnEntry) + phiếu ngày (ReturnNote): service, model,
resolve phiếu ngày, API phiếu ngày lồng và luồng create_and_post atomic.

Gộp từ test_return_entry_service / test_return_entry_model / test_return_note_daily /
test_return_note_api / test_create_and_post_return (tái cấu trúc 2026-08-05).
"""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.return_note_service import ReturnNoteService
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
    ReturnNote,
    ReturnNoteLine,
    ReturnNoteStatus,
    ReturnStatus,
    ReturnType,
    StockItem,
)
from apps.orders.models import Order, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin, make_user_with_permissions
from apps.warehouses.models import Warehouse

# ---------------------------------------------------------------------------
# ReturnEntryService: tạo / ghi sổ / huỷ nhóm đơn hoàn trong phiếu ngày.
# ---------------------------------------------------------------------------


@pytest.fixture
def customer(db):
    return Customer.objects.create(
        full_name="Khach RNE",
        phone_raw="0912345678",
        phone_normalized="0912345678",
        code="CUST-RNE",
    )


def _lines(product, qty=1):
    return [{
        "product_id": product.id,
        "quantity": qty,
        "return_type": ReturnType.DELIVERY_RETURNED,
    }]


def _close_note(note_id):
    """Ép phiếu ngày sang CLOSED — trạng thái chỉ còn xuất hiện ở dữ liệu legacy
    (close_note đã gỡ 2026-07-14), nhưng post/cancel entry vẫn phải xử lý đúng."""
    ReturnNote.objects.filter(id=note_id).update(
        status=ReturnNoteStatus.CLOSED, closed_at=timezone.now()
    )


@pytest.mark.django_db
def test_two_entries_same_day_share_one_note(warehouse_with_two_products):
    wh, p1, p2 = warehouse_with_two_products
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    e2 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p2), shipping_code="SPX2"
    )
    assert e1.return_note_id == e2.return_note_id
    assert ReturnNote.objects.count() == 1
    assert e1.status == ReturnStatus.POSTED


@pytest.mark.django_db
def test_entry_goes_to_new_note_after_close(warehouse_with_two_products):
    wh, p1, p2 = warehouse_with_two_products
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    _close_note(e1.return_note_id)
    e2 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p2), shipping_code="SPX2"
    )
    assert e2.return_note_id != e1.return_note_id
    assert ReturnNote.objects.count() == 2


@pytest.mark.django_db
def test_post_entry_adds_stock(warehouse_with_two_products):
    wh, p1, _ = warehouse_with_two_products
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1, qty=3), shipping_code="SPX1"
    )
    item = StockItem.objects.get(warehouse=wh, product=p1, state="sellable")
    assert item.quantity == 3


@pytest.mark.django_db
def test_cancel_entry_only_reverses_that_entry(warehouse_with_two_products):
    wh, p1, p2 = warehouse_with_two_products
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1, qty=3), shipping_code="SPX1"
    )
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p2, qty=5), shipping_code="SPX2"
    )
    ReturnEntryService.cancel_entry(entry_id=e1.id)

    e1.refresh_from_db()
    assert e1.status == ReturnStatus.CANCELLED
    assert StockItem.objects.get(
        warehouse=wh, product=p1, state="sellable"
    ).quantity == 0
    assert StockItem.objects.get(
        warehouse=wh, product=p2, state="sellable"
    ).quantity == 5


@pytest.mark.django_db
def test_cancel_entry_voids_matching_return_scan(warehouse_with_two_products):
    """Huỷ đơn hoàn → dòng mã quét biến mất khỏi màn hình.

    Trước bản vá: dòng scan kẹt ở `matched` nên vẫn hiện, lại mượn SKU × SL của
    nhóm đơn còn sống cùng mã → một mã bị bắn 3 lần hiện 3 dòng, nhìn như nhập 3.
    """
    wh, p1, _ = warehouse_with_two_products
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.MATCHED,
    )
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    ReturnEntryService.cancel_entry(entry_id=entry.id)

    scan.refresh_from_db()
    assert scan.voided_at is not None
    # Trạng thái GIỮ NGUYÊN: `voided_at` là trục riêng, không đè nghĩa của status.
    assert scan.status == PendingScanStatus.MATCHED


@pytest.mark.django_db
def test_cancel_entry_leaves_dispatch_scan_alone(warehouse_with_two_products):
    """Chỉ thu hồi dòng NHẬP HOÀN — mã quét xuất giao cùng mã không liên quan."""
    wh, p1, _ = warehouse_with_two_products
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.MATCHED,
    )
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    ReturnEntryService.cancel_entry(entry_id=entry.id)

    scan.refresh_from_db()
    assert scan.voided_at is None


@pytest.mark.django_db
def test_duplicate_shipping_code_rejected(warehouse_with_two_products):
    wh, p1, p2 = warehouse_with_two_products
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    with pytest.raises(ValueError, match="SPX1"):
        ReturnEntryService.create_entry(
            warehouse_id=wh.id, lines=_lines(p2), shipping_code="SPX1"
        )


@pytest.mark.django_db
def test_cancel_allowed_after_note_closed(warehouse_with_two_products):
    wh, p1, _ = warehouse_with_two_products
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1, qty=2), shipping_code="SPX1"
    )
    _close_note(e1.return_note_id)
    ReturnEntryService.cancel_entry(entry_id=e1.id)
    e1.refresh_from_db()
    assert e1.status == ReturnStatus.CANCELLED


@pytest.mark.django_db
def test_post_draft_in_closed_note_rehomes_to_open_note(warehouse_with_two_products):
    """Nháp mắc kẹt trong phiếu ĐÃ CHỐT (khớp đơn muộn) → chuyển sang phiếu đang
    mở cùng ngày rồi ghi sổ, cộng tồn đúng 1 lần."""
    wh, p1, _ = warehouse_with_two_products
    entry = ReturnEntryService.create_entry(
        warehouse_id=wh.id, lines=_lines(p1, qty=2), shipping_code="SPX9"
    )
    old_note_id = entry.return_note_id
    _close_note(old_note_id)

    posted = ReturnEntryService.post_entry(entry_id=entry.id)

    assert posted.status == ReturnStatus.POSTED
    assert posted.return_note_id != old_note_id
    assert posted.return_note.code.endswith("-02")
    assert StockItem.objects.get(
        warehouse=wh, product=p1, state="sellable"
    ).quantity == 2


@pytest.mark.django_db
def test_post_non_draft_entry_rejected(warehouse_with_two_products):
    wh, p1, _ = warehouse_with_two_products
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX-POSTED"
    )
    with pytest.raises(ValueError, match="nháp"):
        ReturnEntryService.post_entry(entry_id=entry.id)


@pytest.mark.django_db
def test_create_entry_requires_return_type_per_line(warehouse_with_two_products):
    """Thiếu loại hoàn → chặn, không âm thầm đẩy hàng bảo hành vào tồn bán được."""
    wh, p1, _ = warehouse_with_two_products
    with pytest.raises(ValueError, match="thiếu loại hoàn"):
        ReturnEntryService.create_entry(
            warehouse_id=wh.id,
            lines=[{"product_id": p1.id, "quantity": 1}],
            shipping_code="SPX-NOTYPE",
        )


@pytest.mark.django_db
def test_post_marks_order_returned_and_cancel_reverts_it(warehouse_with_two_products, channel, customer):
    wh, p1, _ = warehouse_with_two_products
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach A",
        phone="0912345678",
        total_amount="100000",
        status=int(OrderStatus.DELIVERED_SUCCESS),
        fulfillment_warehouse=wh,
    )
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        lines=_lines(p1, qty=2),
        shipping_code="SPX-ORD",
        source_order_id=order.id,
    )
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.RETURNED)
    assert entry.order_status_before == int(OrderStatus.DELIVERED_SUCCESS)

    ReturnEntryService.cancel_entry(entry_id=entry.id)
    order.refresh_from_db()
    entry.refresh_from_db()
    assert int(order.status) == int(OrderStatus.DELIVERED_SUCCESS)
    assert entry.status == ReturnStatus.CANCELLED
    assert entry.order_status_before is None


@pytest.fixture
def issued_order(warehouse_with_two_products, channel, customer):
    """Đơn đã xuất kho 2 đơn vị p1 (tồn đầu 10 → còn 8)."""
    wh, p1, _ = warehouse_with_two_products
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach Guard",
        phone="0912345678",
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
    return order


def _sellable(wh, product):
    item = StockItem.objects.filter(
        warehouse=wh, product=product, state="sellable"
    ).first()
    return item.quantity if item else 0


@pytest.mark.django_db
def test_second_entry_for_same_order_cannot_credit_twice(warehouse_with_two_products, issued_order):
    """Money invariant: 2 đơn vị đã trừ tồn chỉ được cộng lại đúng 1 lần."""
    wh, p1, _ = warehouse_with_two_products
    assert _sellable(wh, p1) == 8

    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        lines=_lines(p1, qty=2),
        shipping_code="SPX-G1",
        source_order_id=issued_order.id,
    )
    assert _sellable(wh, p1) == 10

    entry_b = ReturnEntryService.create_entry(
        warehouse_id=wh.id,
        lines=_lines(p1, qty=2),
        shipping_code="SPX-G2",
        source_order_id=issued_order.id,
    )
    with pytest.raises(ValueError, match="chỉ còn 0 đơn vị"):
        ReturnEntryService.post_entry(entry_id=entry_b.id)

    entry_b.refresh_from_db()
    assert entry_b.status == ReturnStatus.DRAFT
    assert _sellable(wh, p1) == 10


@pytest.mark.django_db
def test_release_for_order_does_not_credit_returned_units_again(warehouse_with_two_products, issued_order):
    """Hoàn đã ghi sổ rồi mới huỷ đơn → release không cộng tồn lần 2."""
    wh, p1, _ = warehouse_with_two_products
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        lines=_lines(p1, qty=2),
        shipping_code="SPX-REL",
        source_order_id=issued_order.id,
    )
    assert _sellable(wh, p1) == 10

    StockSalesService.release_for_order(order_id=issued_order.id)

    assert _sellable(wh, p1) == 10


@pytest.mark.django_db
def test_update_note_allowed_after_posted(warehouse_with_two_products):
    wh, p1, _ = warehouse_with_two_products
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX-NOTE"
    )
    entry = ReturnEntryService.update_note(entry_id=entry.id, note="ghi chú mới")
    assert entry.note == "ghi chú mới"
    assert entry.status == ReturnStatus.POSTED


# ---------------------------------------------------------------------------
# Model / constraint mức entity: phiếu ngày OPEN duy nhất, mã vận đơn nháp,
# return_type và quantity ở dòng.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_only_one_open_note_per_day_and_warehouse(warehouse):
    today = timezone.localdate()
    ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=today, status=ReturnNoteStatus.OPEN
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnNote.objects.create(
            code="PTH-2", warehouse=warehouse, doc_date=today,
            status=ReturnNoteStatus.OPEN,
        )


@pytest.mark.django_db
def test_closed_note_does_not_block_new_open_note(warehouse):
    today = timezone.localdate()
    ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=today,
        status=ReturnNoteStatus.CLOSED, closed_at=timezone.now(),
    )
    note = ReturnNote.objects.create(
        code="PTH-2", warehouse=warehouse, doc_date=today, status=ReturnNoteStatus.OPEN
    )
    # Cả 2 phiếu cùng kho/cùng ngày tồn tại song song: unique chỉ áp cho phiếu ĐANG MỞ.
    assert list(
        ReturnNote.objects.filter(warehouse=warehouse, doc_date=today)
        .order_by("code")
        .values_list("code", "status")
    ) == [("PTH-1", ReturnNoteStatus.CLOSED), ("PTH-2", ReturnNoteStatus.OPEN)]
    assert ReturnNote.objects.get(warehouse=warehouse, doc_date=today, status=ReturnNoteStatus.OPEN) == note


@pytest.mark.django_db
def test_entry_shipping_code_unique_when_draft_unmatched(warehouse):
    note = ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=timezone.localdate()
    )
    ReturnEntry.objects.create(
        return_note=note, shipping_code="SPX1", status=ReturnStatus.DRAFT
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnEntry.objects.create(
            return_note=note, shipping_code="SPX1", status=ReturnStatus.DRAFT
        )


@pytest.mark.django_db
def test_line_requires_return_type(warehouse):
    note = ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=timezone.localdate()
    )
    entry = ReturnEntry.objects.create(return_note=note, status=ReturnStatus.DRAFT)
    product = Product.objects.create(sku="SKU1", name="SP 1")
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnNoteLine.objects.create(
            entry=entry, product=product, quantity=1, return_type=""
        )
    line = ReturnNoteLine.objects.create(
        entry=entry, product=product, quantity=1,
        return_type=ReturnType.DELIVERY_RETURNED,
    )
    assert line.entry_id == entry.id


@pytest.mark.django_db
def test_line_quantity_must_be_positive(warehouse):
    # Audit followup 2026-07 mục 4.3: quantity=0 vô nghĩa (không hoàn gì); constraint
    # là lưới an toàn cho đường ORM/bulk_create trực tiếp (serializer đã chặn
    # min_value=1 ở tầng API).
    note = ReturnNote.objects.create(
        code="PTH-QTY", warehouse=warehouse, doc_date=timezone.localdate()
    )
    entry = ReturnEntry.objects.create(return_note=note, status=ReturnStatus.DRAFT)
    product = Product.objects.create(sku="SKU-QTY", name="SP Qty")
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnNoteLine.objects.create(
            entry=entry, product=product, quantity=0,
            return_type=ReturnType.DELIVERY_RETURNED,
        )


# ---------------------------------------------------------------------------
# ReturnNoteService.resolve_open_note: phiếu ngày theo kho, tái dùng / hậu tố -02.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolve_open_note_reuses_same_note(warehouse):
    today = timezone.localdate()
    a = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    b = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    assert a.id == b.id
    assert a.code == f"PTH-{today:%Y%m%d}-K{warehouse.id}"
    assert ReturnNote.objects.count() == 1


@pytest.mark.django_db
def test_closed_note_forces_new_note_with_suffix(warehouse):
    """Phiếu CLOSED (legacy / lệnh gộp hồi tố) không được tái dùng — mã mới có -02."""
    today = timezone.localdate()
    first = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    ReturnNote.objects.filter(id=first.id).update(
        status=ReturnNoteStatus.CLOSED, closed_at=timezone.now()
    )
    second = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    assert second.id != first.id
    assert second.code == f"PTH-{today:%Y%m%d}-K{warehouse.id}-02"


@pytest.mark.django_db
def test_today_vn_is_local_date(warehouse):
    assert ReturnNoteService.today_vn() == timezone.localdate()


# ---------------------------------------------------------------------------
# API nhóm đơn hoàn sau khi gỡ UI phiếu trả (2026-07-14): tạo entry → phiếu ngày
# lồng, huỷ entry; các endpoint phiếu ngày (/return-notes/*) không còn tồn tại.
# ---------------------------------------------------------------------------


@pytest.fixture
def return_note_api(db):
    # make_admin: role hệ thống → có returns:view/create/approve (RBAC ở đây là
    # role-based, is_superuser KHÔNG bypass require_section).
    user = make_admin("admin_return_note")
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
def test_create_return_returns_entry_with_daily_note(return_note_api, warehouse):
    client, _ = return_note_api
    p = Product.objects.create(sku="SKU1", name="SP")
    res = client.post(
        "/api/inventory/returns",
        {
            "warehouse_id": warehouse.id,
            "shipping_code": "SPX1",
            "auto_post": True,
            "lines": [
                {"product_id": p.id, "quantity": 2,
                 "return_type": ReturnType.DELIVERY_RETURNED},
            ],
        },
        format="json",
    )
    assert res.status_code == 201, res.data
    assert res.data["status"] == ReturnStatus.POSTED
    # Key phiếu ngày là "return_note" — "note" đã là ghi chú của entry.
    assert res.data["return_note"]["code"].startswith("PTH-")
    assert res.data["return_note"]["warehouse_name"] == warehouse.name
    assert res.data["return_note"]["status"] == ReturnNoteStatus.OPEN


@pytest.mark.django_db
def test_cancel_entry_reverses_stock(return_note_api, warehouse):
    client, _ = return_note_api
    p = Product.objects.create(sku="SKU1", name="SP")
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=warehouse.id, shipping_code="SPX1",
        lines=[{"product_id": p.id, "quantity": 1,
                "return_type": ReturnType.DELIVERY_RETURNED}],
    )

    res = client.post(f"/api/inventory/returns/{entry.id}/cancel")
    assert res.status_code == 200, res.data
    assert res.data["status"] == ReturnStatus.CANCELLED

    stock = client.get(
        "/api/inventory/stock", {"limit": 50, "offset": 0, "state": "sellable"}
    )
    items = [i for i in stock.json()["items"] if i["product_sku"] == "SKU1"]
    assert (items[0]["quantity"] if items else 0) == 0


@pytest.mark.django_db
def test_return_note_endpoints_removed(return_note_api, warehouse):
    """3 endpoint phiếu ngày (detail/close/cancel) đã gỡ — route phải 404."""
    client, _ = return_note_api
    p = Product.objects.create(sku="SKU1", name="SP")
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=warehouse.id, shipping_code="SPX1",
        lines=[{"product_id": p.id, "quantity": 1,
                "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    note_id = entry.return_note_id
    assert client.get(f"/api/inventory/return-notes/{note_id}").status_code == 404
    assert client.post(f"/api/inventory/return-notes/{note_id}/close").status_code == 404
    assert client.post(f"/api/inventory/return-notes/{note_id}/cancel").status_code == 404


@pytest.mark.django_db
def test_cancel_entry_requires_approve(warehouse):
    """returns:view/create/edit (không approve) → 403 khi huỷ nhóm đơn hoàn."""
    staff = make_user_with_permissions(
        "returns-staff-note", ["returns:view", "returns:create", "returns:edit"]
    )
    client = APIClient()
    client.force_authenticate(staff)
    p = Product.objects.create(sku="SKU1", name="SP")
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=warehouse.id, shipping_code="SPX1",
        lines=[{"product_id": p.id, "quantity": 1,
                "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    assert client.post(f"/api/inventory/returns/{entry.id}/cancel").status_code == 403


class CreateAndPostReturnTests(TestCase):
    """create_and_post_entry: ghi sổ ngay khi tạo nhóm đơn hoàn (luồng quét khớp mã) —
    atomic: post lỗi thì không nhóm đơn nào tồn tại, status đơn không đổi."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-CAP", name="Kho CAP")
        self.p1 = Product.objects.create(name="SP1", sku="SKU-CAP1")
        self.channel = Channel.objects.create(code="ch-cap", name="CAP Channel")
        self.customer = Customer.objects.create(
            full_name="Khach CAP",
            phone_raw="0912345671",
            phone_normalized="0912345671",
            code="CUST-CAP",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach CAP",
            phone="0912345671",
            total_amount="100000",
            fulfillment_warehouse=self.warehouse,
        )

    def _qty(self, product, state):
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=product, state=state
        ).first()
        return item.quantity if item else 0

    def _lines(self, qty, return_type=ReturnType.DELIVERY_RETURNED):
        return [{
            "product_id": self.p1.id,
            "quantity": qty,
            "return_type": return_type,
        }]

    def test_happy_path_posts_entry_marks_order_and_moves_stock(self):
        entry = ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=self._lines(2),
            source_order_id=self.order.id,
        )
        self.assertEqual(entry.status, "posted")
        self.assertIsNotNone(entry.posted_at)
        self.assertEqual(self._qty(self.p1, "sellable"), 2)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), int(OrderStatus.RETURNED))

    def test_duplicate_shipping_code_blocked(self):
        """Đã có nhóm đơn chưa huỷ cùng mã vận đơn → nhóm thứ 2 bị chặn
        (chống 1 đơn khớp hoàn 2 lần / cộng tồn gấp đôi)."""
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=self._lines(1),
            source_order_id=self.order.id,
            shipping_code="TRACK-DUP-1",
        )
        self.assertEqual(self._qty(self.p1, "sellable"), 1)
        with self.assertRaises(ValueError):
            ReturnEntryService.create_and_post_entry(
                warehouse_id=self.warehouse.id,
                lines=self._lines(1),
                source_order_id=self.order.id,
                shipping_code="TRACK-DUP-1",
            )
        # Vẫn 1 nhóm đơn, tồn vẫn 1 — không nhân đôi.
        self.assertEqual(ReturnEntry.objects.count(), 1)
        self.assertEqual(self._qty(self.p1, "sellable"), 1)

    def test_cancelled_entry_does_not_block_recreate(self):
        """Nhóm đơn cũ đã huỷ không chặn tạo lại cùng mã vận đơn."""
        entry = ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=self._lines(1),
            source_order_id=self.order.id,
            shipping_code="TRACK-DUP-2",
        )
        ReturnEntryService.cancel_entry(entry_id=entry.id)
        entry2 = ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=self._lines(1),
            source_order_id=self.order.id,
            shipping_code="TRACK-DUP-2",
        )
        self.assertEqual(entry2.status, "posted")
        self.assertEqual(self._qty(self.p1, "sellable"), 1)

    def test_rollback_on_failure_creates_nothing(self):
        before = int(self.order.status)
        # Dòng thiếu loại hoàn → build_lines raise "thiếu loại hoàn" →
        # transaction rollback toàn bộ (kể cả phiếu ngày vừa mở).
        with self.assertRaises(ValueError):
            ReturnEntryService.create_and_post_entry(
                warehouse_id=self.warehouse.id,
                lines=[{"product_id": self.p1.id, "quantity": 1}],
                source_order_id=self.order.id,
            )
        self.assertEqual(ReturnEntry.objects.count(), 0)
        self.assertEqual(ReturnNote.objects.count(), 0)
        self.order.refresh_from_db()
        self.assertEqual(int(self.order.status), before)
