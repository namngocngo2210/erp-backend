"""Khớp mã pending (cron + manual): match, guard đơn hủy, đua claim, ngày ghi sổ.

Gộp từ test_pending_scan_service_match / _cancelled / _claim_race / _occurred_at
(đợt tái cấu trúc test, xem docs/test-restructure-2026-08-05.md).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from datetime import UTC
from unittest.mock import patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.db import connection
from django.utils import timezone

from apps.customers.models import Customer
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnEntry,
    ReturnStatus,
    StockItem,
    StockMovement,
    StockState,
)
from apps.inventory.tests.helpers import make_order as _make_order
from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    VerificationUnavailable,
)
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _sellable_qty(warehouse_id: int, product_id: int) -> int:
    item = StockItem.objects.filter(
        warehouse_id=warehouse_id, product_id=product_id, state=StockState.SELLABLE
    ).first()
    return item.quantity if item else 0


@pytest.fixture
def order_awaiting_shipment(channel, warehouse):
    # Order status AWAITING_SHIPMENT(11), shipping_code="SPX1",
    # có 1 OrderItem (product, qty=2) và tồn kho đủ ở `warehouse`.
    return _make_order(channel, warehouse, "1", "SPX1", qty=2)


@pytest.fixture
def two_orders_same_code(channel, warehouse):
    # 2 Order cùng shipping_code="SPX2", đơn tạo sau có id lớn hơn.
    first = _make_order(channel, warehouse, "2a", "SPX2", qty=1)
    second = _make_order(channel, warehouse, "2b", "SPX2", qty=1)
    return [first, second]


# ─────────────────────── Service match (cron + manual) ───────────────────────


def test_no_order_yet_increments_attempts(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="NOPE", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert summary["unmatched"] == 1
    assert scan.status == PendingScanStatus.PENDING
    assert scan.attempts == 1
    assert scan.last_attempt_at is not None


def test_dispatch_scan_matches_and_issues_stock(order_awaiting_shipment, warehouse, staff_user):
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert summary["matched"] == 1
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.matched_order_id == order_awaiting_shipment.id
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=order_awaiting_shipment.id,
        movement_type=MovementType.SALE,
    ).exists()


def test_return_scan_posts_note_and_credits_stock(order_awaiting_shipment, warehouse, staff_user):
    # Khớp = ghi sổ = cộng tồn, giống hệt luồng quét khớp-ngay (auto_post). Nếu chỉ
    # tạo nháp thì mã hiện "đã khớp" nhưng tồn chưa về → kho đếm thiếu.
    product_id = order_awaiting_shipment.items.first().product_id
    before = _sellable_qty(warehouse.id, product_id)
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", scanned_by=staff_user,
    )
    PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    entry = ReturnEntry.objects.get(shipping_code="SPX1")
    assert entry.status == ReturnStatus.POSTED
    assert entry.source_order_id == order_awaiting_shipment.id
    assert entry.lines.count() >= 1
    # result_ref = mã PHIẾU NGÀY (entry không còn mã riêng).
    assert scan.result_ref == entry.return_note.code
    assert StockMovement.objects.filter(
        reference_type="return_entry", reference_id=entry.id,
        movement_type=MovementType.RETURN_IN,
    ).exists()
    # Tồn THẬT phải cộng vào đúng bucket bán được (movement tồn tại chưa đủ:
    # số dư mới là thứ kho đếm).
    item = StockItem.objects.get(
        warehouse_id=warehouse.id, product_id=product_id, state=StockState.SELLABLE
    )
    assert item.quantity - before == 2  # đúng SL của đơn
    assert item.state == StockState.SELLABLE


def test_matched_scan_creates_entry_in_daily_note(order_awaiting_shipment, warehouse, staff_user):
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", scanned_by=staff_user,
    )
    PendingScanService.match_pending_scans()
    entry = ReturnEntry.objects.get(shipping_code=scan.code)
    assert entry.status == ReturnStatus.POSTED
    assert entry.return_note.code.startswith("PTH-")
    assert entry.return_note.warehouse_id == warehouse.id


def test_entry_lands_in_note_of_the_day_the_code_was_scanned(
    order_awaiting_shipment, warehouse, staff_user
):
    # Mã quét lúc 00:30 ngày 04/07 giờ VN (= 17:30 UTC ngày 03/07), cron khớp được
    # sau đó → nhóm đơn phải vào phiếu ngày 04/07 (ngày hàng về kho theo giờ VN),
    # KHÔNG phải 03/07 (ngày UTC) và cũng không phải ngày cron chạy.
    # Ngày cố định cứng: nếu implementation dùng scan.created_at.date() (naive UTC)
    # thì test này FAIL, khác với so sánh lại chính localtime() của implementation.
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", scanned_by=staff_user,
    )
    # auto_now_add bỏ qua kwarg lúc create → phải UPDATE mới ghi đè được created_at.
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=datetime(2026, 7, 3, 17, 30, tzinfo=UTC)
    )
    PendingScanService.match_pending_scans()
    entry = ReturnEntry.objects.get(shipping_code="SPX1")
    assert entry.return_note.doc_date == date(2026, 7, 4)
    assert "20260704" in entry.return_note.code


def test_return_scan_uses_scan_return_type_on_lines(order_awaiting_shipment, warehouse, staff_user):
    PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="warranty", scanned_by=staff_user,
    )
    PendingScanService.match_pending_scans()
    entry = ReturnEntry.objects.get(shipping_code="SPX1")
    assert {line.return_type for line in entry.lines.all()} == {"warranty"}


def test_multiple_orders_picks_newest_and_notes(two_orders_same_code, warehouse, staff_user):
    newest = max(two_orders_same_code, key=lambda o: o.id)
    scan = PendingShipmentScan.objects.create(
        code="SPX2", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.matched_order_id == newest.id
    assert "Nhiều đơn" in scan.error


def test_business_error_marks_failed(order_awaiting_shipment, warehouse, staff_user, monkeypatch):
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    from apps.orders.application.services.order_service_impl import OrderService

    monkeypatch.setattr(
        OrderService, "dispatch_orders_batch",
        staticmethod(lambda **kw: [{"order_id": kw["order_ids"][0], "ok": False, "error": "boom"}]),
    )
    summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert summary["failed"] == 1
    assert scan.status == PendingScanStatus.FAILED
    assert "boom" in scan.error


def test_matched_and_failed_not_reprocessed(warehouse):
    PendingShipmentScan.objects.create(
        code="A", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.create(
        code="B", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.FAILED,
    )
    summary = PendingScanService.match_pending_scans()
    assert summary["processed"] == 0


def test_return_scan_reuses_existing_note_no_duplicate(
    order_awaiting_shipment, warehouse, staff_user
):
    # Quét tay đã tạo nhóm đơn nháp cho mã này TRƯỚC KHI cron chạy (vd: đơn vừa sync
    # xong, NV kho quét tay ngay) — cron không được tạo nhóm đơn thứ 2 cho cùng mã.
    existing = ReturnEntryService.create_entry(
        warehouse_id=warehouse.id,
        lines=[
            {
                "product_id": order_awaiting_shipment.items.first().product_id,
                "quantity": 2,
                "return_type": "delivery_returned",
            }
        ],
        source_order_id=order_awaiting_shipment.id,
        shipping_code="SPX1",
        customer_name=order_awaiting_shipment.name,
        customer_phone=order_awaiting_shipment.phone,
        user_id=staff_user.id,
    )
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", scanned_by=staff_user,
    )
    PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.result_ref == existing.return_note.code
    assert ReturnEntry.objects.filter(shipping_code="SPX1").count() == 1


def test_manual_match_dispatch_uses_chosen_order(
    order_awaiting_shipment, warehouse, staff_user
):
    # Mã quét KHÔNG trùng shipping_code của đơn — khớp tay theo đơn người dùng chọn.
    scan = PendingShipmentScan.objects.create(
        code="UNMATCHED-VD", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        scanned_by=staff_user,
    )
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order_awaiting_shipment.id, actor=staff_user
    )
    assert result["ok"] is True
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.matched_order_id == order_awaiting_shipment.id
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=order_awaiting_shipment.id,
        movement_type=MovementType.SALE,
    ).exists()


def test_manual_match_return_posts_note_and_credits_stock(
    order_awaiting_shipment, warehouse, staff_user
):
    # Trang Lịch sử nhập xuất → "Khớp" → chọn mã hoá đơn: phải cộng tồn ngay.
    product_id = order_awaiting_shipment.items.first().product_id
    before = _sellable_qty(warehouse.id, product_id)
    scan = PendingShipmentScan.objects.create(
        code="UNMATCHED-RET", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", scanned_by=staff_user,
    )
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order_awaiting_shipment.id, actor=staff_user
    )
    assert result["ok"] is True
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    entry = ReturnEntry.objects.get(shipping_code="UNMATCHED-RET")
    assert entry.status == ReturnStatus.POSTED
    assert entry.source_order_id == order_awaiting_shipment.id
    assert scan.result_ref == entry.return_note.code
    after = _sellable_qty(warehouse.id, product_id)
    assert after - before == 2  # đúng SL của đơn


def test_manual_match_return_guard_failure_creates_no_note(
    order_awaiting_shipment, warehouse, staff_user, monkeypatch
):
    # Ghi sổ bị guard chặn (đơn đã được hoàn kho trước đó) → scan FAILED kèm lỗi
    # nguyên văn, KHÔNG để lại nhóm đơn nháp mồ côi (create + post cùng 1 transaction).
    monkeypatch.setattr(
        ReturnEntryService, "post_entry",
        staticmethod(lambda **kw: (_ for _ in ()).throw(ValueError("đã cộng lại tồn"))),
    )
    scan = PendingShipmentScan.objects.create(
        code="GUARDED-RET", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", scanned_by=staff_user,
    )
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order_awaiting_shipment.id, actor=staff_user
    )
    assert result["ok"] is False
    assert "đã cộng lại tồn" in result["error"]
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.FAILED
    assert "đã cộng lại tồn" in scan.error
    assert not ReturnEntry.objects.filter(shipping_code="GUARDED-RET").exists()


def test_manual_match_missing_order_keeps_pending(warehouse, staff_user):
    scan = PendingShipmentScan.objects.create(
        code="X", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=999999, actor=staff_user
    )
    assert result["ok"] is False
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING


def test_soft_time_limit_exceeded_propagates_and_keeps_pending(
    order_awaiting_shipment, warehouse, staff_user, monkeypatch
):
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    from apps.orders.application.services.order_service_impl import OrderService

    def _raise(**kw):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(OrderService, "dispatch_orders_batch", staticmethod(_raise))

    with pytest.raises(SoftTimeLimitExceeded):
        PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING
    assert scan.attempts == 0


# ─────────────────────── Guard đơn hủy (KiotViet verifier) ───────────────────────
# Cron khớp mã pending KHÔNG được tự xuất kho đơn đã hủy.
#
# Ba tính chất phải giữ:
# 1. Đơn hủy (DB hoặc KiotViet) → scan chuyển ``cancelled``, KHÔNG có StockMovement SALE.
# 2. Không xác minh được (KiotViet timeout/5xx) → scan GIỮ ``pending`` để cron thử lại
#    vòng sau (nếu để ``failed`` thì một cú timeout thoáng qua làm mã kẹt vĩnh viễn —
#    ``match_pending_scans`` chỉ quét status=pending).
# 3. Lời gọi HTTP tới KiotViet KHÔNG được nằm trong transaction (giữ lock + connection
#    suốt thời gian chờ mạng, và 200 scan × timeout > soft_time_limit của task).

# Verifier được import cục bộ trong hàm → patch thẳng trên class ở module nguồn.
_VERIFY = (
    "apps.kiotviet.application.services.cancellation_verifier."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


def _make_order_cancel(channel, warehouse, shipping_code: str, **kwargs) -> Order:
    suffix = uuid.uuid4().hex[:8]
    customer = Customer.objects.create(
        full_name=f"KH {suffix}",
        phone_raw=f"09{suffix}",
        phone_normalized=f"09{suffix}",
        code=f"CUST-SC-{suffix}",
    )
    product = Product.objects.create(name=f"SP {suffix}", sku=f"SKU-SC-{suffix}")
    kwargs.setdefault("status", OrderStatus.AWAITING_SHIPMENT)
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        order_code=f"ORD-{suffix}",
        name=f"KH {suffix}",
        phone=f"09{suffix}",
        total_amount="200000",
        shipping_code=shipping_code,
        fulfillment_warehouse=warehouse,
        **kwargs,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=2,
        unit_price=100000,
        line_total=200000,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    return order


def _scan(code: str, warehouse, user) -> PendingShipmentScan:
    return PendingShipmentScan.objects.create(
        code=code,
        kind=PendingScanKind.DISPATCH,
        status=PendingScanStatus.PENDING,
        warehouse=warehouse,
        scanned_by=user,
    )


def _has_sale_movement(order: Order) -> bool:
    return StockMovement.objects.filter(
        reference_type="order",
        reference_id=order.id,
        movement_type=MovementType.SALE,
    ).exists()


def test_kiotviet_cancelled_marks_scan_cancelled_and_skips_stock(
    channel, warehouse, staff_user
):
    code = f"SC{uuid.uuid4().hex[:8]}"
    order = _make_order_cancel(channel, warehouse, code)
    scan = _scan(code, warehouse, staff_user)

    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        summary = PendingScanService.match_pending_scans()

    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.CANCELLED
    assert scan.matched_order_id == order.id
    # Đơn hủy là kết quả nghiệp vụ, KHÔNG phải lỗi hệ thống → metric tách riêng.
    assert summary["cancelled"] == 1
    assert summary["failed"] == 0
    assert not _has_sale_movement(order)


def test_db_cancelled_order_marks_scan_cancelled(channel, warehouse, staff_user):
    code = f"SC{uuid.uuid4().hex[:8]}"
    order = _make_order_cancel(channel, warehouse, code, status=OrderStatus.CANCELLED)
    scan = _scan(code, warehouse, staff_user)

    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        PendingScanService.match_pending_scans()

    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.CANCELLED
    assert not _has_sale_movement(order)


def test_verify_unavailable_keeps_scan_pending_then_matches_after_backoff(
    channel, warehouse, staff_user
):
    code = f"SC{uuid.uuid4().hex[:8]}"
    order = _make_order_cancel(channel, warehouse, code)
    scan = _scan(code, warehouse, staff_user)

    # Vòng 1: KiotViet chết → KHÔNG được đánh failed (cron chỉ quét pending → kẹt vĩnh viễn).
    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING
    assert scan.attempts == 1
    assert summary["failed"] == 0
    assert not _has_sale_movement(order)

    # Vòng 2 (ngay lập tức): mã đang trong backoff → KHÔNG thử lại, không ăn slot của mã mới.
    with patch(_VERIFY, return_value=CancelCheck.OK) as verify:
        PendingScanService.match_pending_scans()
    verify.assert_not_called()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING
    assert scan.attempts == 1

    # Hết backoff (mô phỏng: đẩy last_attempt_at lùi lại) → khớp bình thường, không cần khớp tay.
    PendingShipmentScan.objects.filter(id=scan.id).update(
        last_attempt_at=timezone.now() - timedelta(minutes=10)
    )
    with patch(_VERIFY, return_value=CancelCheck.OK):
        summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert summary["matched"] == 1
    assert _has_sale_movement(order)


def test_backoff_does_not_delay_freshly_scanned_codes(channel, warehouse, staff_user):
    """Mã "độc" (verify hỏng vĩnh viễn) không được chiếm chỗ của mã MỚI quét.

    Trước đây hàng đợi sort theo created_at → mã hỏng luôn đứng đầu và ăn ngân sách
    dispatch mỗi vòng (head-of-line blocking).
    """
    poison_code = f"SC{uuid.uuid4().hex[:8]}"
    poison_order = _make_order_cancel(channel, warehouse, poison_code)
    poison = _scan(poison_code, warehouse, staff_user)

    with patch(_VERIFY, side_effect=VerificationUnavailable("payload lạ")):
        PendingScanService.match_pending_scans()
    poison.refresh_from_db()
    assert poison.status == PendingScanStatus.PENDING

    # Mã mới quét ngay sau đó: vòng cron kế tiếp phải xử lý nó (bỏ qua mã đang backoff).
    fresh_code = f"SC{uuid.uuid4().hex[:8]}"
    fresh_order = _make_order_cancel(channel, warehouse, fresh_code)
    fresh = _scan(fresh_code, warehouse, staff_user)

    verified: list[int] = []

    def _verify(order):
        verified.append(order.id)
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        PendingScanService.match_pending_scans()

    assert verified == [fresh_order.id]
    fresh.refresh_from_db()
    assert fresh.status == PendingScanStatus.MATCHED
    assert _has_sale_movement(fresh_order)
    poison.refresh_from_db()
    assert poison.status == PendingScanStatus.PENDING
    assert not _has_sale_movement(poison_order)


def test_verifier_is_never_called_inside_a_transaction(channel, warehouse, staff_user):
    """HTTP KiotViet nằm ngoài transaction: mỗi atomic block do code sản phẩm mở sẽ
    thêm 1 savepoint (test đã chạy trong atomic của pytest-django) → độ sâu savepoint
    lúc verifier chạy phải bằng đúng độ sâu lúc chưa vào service."""
    code = f"SC{uuid.uuid4().hex[:8]}"
    _make_order_cancel(channel, warehouse, code)
    _scan(code, warehouse, staff_user)

    baseline = len(connection.savepoint_ids)
    depths: list[int] = []

    def _verify(order):
        depths.append(len(connection.savepoint_ids))
        return CancelCheck.OK

    with patch(_VERIFY, side_effect=_verify):
        PendingScanService.match_pending_scans()

    assert depths, "verifier phải được gọi (nếu không, guard đơn hủy đã bị bỏ qua)"
    assert all(d == baseline for d in depths), (
        f"verifier gọi bên trong transaction (savepoint depth {depths} != {baseline})"
    )


def test_manual_match_cancelled_order_marks_scan_cancelled(
    channel, warehouse, staff_user
):
    order = _make_order_cancel(channel, warehouse, "OTHER-CODE")
    scan = _scan(f"SC{uuid.uuid4().hex[:8]}", warehouse, staff_user)

    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        result = PendingScanService.manual_match(
            scan_id=scan.id, order_id=order.id, actor=staff_user
        )

    assert result["ok"] is False
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.CANCELLED
    assert not _has_sale_movement(order)


def test_manual_match_verify_unavailable_keeps_scan_pending(
    channel, warehouse, staff_user
):
    order = _make_order_cancel(channel, warehouse, "OTHER-CODE-2")
    scan = _scan(f"SC{uuid.uuid4().hex[:8]}", warehouse, staff_user)

    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        result = PendingScanService.manual_match(
            scan_id=scan.id, order_id=order.id, actor=staff_user
        )

    assert result["ok"] is False
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING
    assert not _has_sale_movement(order)


# ─────────────────────── Đua claim: manual match vs cron ───────────────────────
# Manual match đua với cron trong cửa sổ HTTP verify.
#
# Cả hai đường đều chủ đích nhả lock scan trước khi gọi KiotViet; bước finalize
# re-check được trạng thái NHƯNG việc trừ kho đã commit trước đó ở cả hai phía —
# 2 đơn khác nhau cùng bị trừ kho từ 1 lần quét. Fix: txn #1 "claim" scan
# (`claimed_at`); đường kia thấy claim còn hạn thì từ chối ngay, không dispatch.


def _fake_dispatch_factory(calls, nested=None):
    """_complete_dispatch giả: ghi lại lời gọi; lời gọi ĐẦU chạy `nested` giữa chừng
    (mô phỏng việc khác chen vào đúng cửa sổ HTTP)."""

    def fake(scan, order, actor=None, *, warehouse_id=None):
        calls.append(order.id)
        if nested is not None and len(calls) == 1:
            nested()
        return f"order:{order.id}", "đã xuất kho.", []

    return fake


def test_manual_match_during_cron_http_window_is_rejected(
    monkeypatch, channel, warehouse
):
    first = _make_order(channel, warehouse, "c1", "SPXC1", qty=1)
    second = _make_order(channel, warehouse, "c2", "SPXC1", qty=1)
    scan = PendingShipmentScan.objects.create(
        code="SPXC1", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )

    calls: list[int] = []
    inner_result: dict = {}

    def nested_manual_match():
        inner_result.update(
            PendingScanService.manual_match(scan_id=scan.id, order_id=first.id)
        )

    monkeypatch.setattr(
        PendingScanService,
        "_complete_dispatch",
        staticmethod(_fake_dispatch_factory(calls, nested=nested_manual_match)),
    )

    outcome, used_remote = PendingScanService._process_one(scan.id)

    assert outcome == "matched"
    # Manual match chen giữa cửa sổ HTTP phải bị TỪ CHỐI — không dispatch đơn thứ 2.
    assert inner_result.get("ok") is False
    assert calls == [second.id], (
        "chỉ được dispatch đúng 1 đơn (đơn cron chọn); "
        f"thực tế đã dispatch: {calls}"
    )
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.matched_order_id == second.id
    # Claim phải được giải phóng sau khi finalize xong.
    assert scan.claimed_at is None


def test_cron_skips_scan_claimed_by_manual_match(monkeypatch, channel, warehouse):
    _make_order(channel, warehouse, "c3", "SPXC2", qty=1)
    scan = PendingShipmentScan.objects.create(
        code="SPXC2",
        kind=PendingScanKind.DISPATCH,
        warehouse=warehouse,
        claimed_at=timezone.now(),
    )
    calls: list[int] = []
    monkeypatch.setattr(
        PendingScanService,
        "_complete_dispatch",
        staticmethod(_fake_dispatch_factory(calls)),
    )

    outcome, used_remote = PendingScanService._process_one(scan.id)

    assert outcome is None
    assert used_remote is False
    assert calls == []
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING


def test_expired_claim_does_not_block_cron(monkeypatch, channel, warehouse):
    order = _make_order(channel, warehouse, "c4", "SPXC3", qty=1)
    scan = PendingShipmentScan.objects.create(
        code="SPXC3",
        kind=PendingScanKind.DISPATCH,
        warehouse=warehouse,
        claimed_at=timezone.now() - timedelta(minutes=11),
    )
    calls: list[int] = []
    monkeypatch.setattr(
        PendingScanService,
        "_complete_dispatch",
        staticmethod(_fake_dispatch_factory(calls)),
    )

    outcome, _ = PendingScanService._process_one(scan.id)

    assert outcome == "matched"
    assert calls == [order.id]
    scan.refresh_from_db()
    assert scan.claimed_at is None


# ─────────────────────── Ngày ghi sổ (occurred_at) ───────────────────────
# Khớp mã chờ phải ghi sổ theo NGÀY QUÉT, không phải ngày cron chạy.
#
# Xem docs/superpowers/specs/2026-07-22-scan-match-business-date-design.md

# 03/07 17:30 UTC = 04/07 00:30 giờ VN. Ngày cố định cứng để bắt được lỗi dùng
# `.date()` trên giá trị UTC thay vì `timezone.localtime(...)`.
SCANNED_AT = datetime(2026, 7, 3, 17, 30, tzinfo=UTC)
SCANNED_LOCAL_DATE = date(2026, 7, 4)


@pytest.fixture
def order(make_order, channel, warehouse):
    product = Product.objects.create(name="SP Scan Date", sku="SKU-SD-1")
    return make_order(
        channel=channel, warehouse=warehouse, product=product, quantity=2, stock_qty=10,
        name="Khach Scan Date", phone="0929000111", customer_code="CUST-SD-1",
        shipping_code="SDX1",
    )


def _scan_backdated(*, warehouse, staff_user, kind, return_type="") -> PendingShipmentScan:
    scan = PendingShipmentScan.objects.create(
        code="SDX1",
        kind=kind,
        warehouse=warehouse,
        return_type=return_type,
        scanned_by=staff_user,
    )
    # auto_now_add bỏ qua kwarg lúc create → phải UPDATE mới ghi đè được created_at.
    PendingShipmentScan.objects.filter(pk=scan.pk).update(created_at=SCANNED_AT)
    scan.refresh_from_db()
    return scan


def test_dispatch_match_stamps_scan_date_not_cron_date(order, warehouse, staff_user):
    """Quét 04/07, cron khớp hôm nay → bút toán xuất thuộc ngày 04/07."""
    _scan_backdated(warehouse=warehouse, staff_user=staff_user, kind=PendingScanKind.DISPATCH)

    PendingScanService.match_pending_scans()

    movement = StockMovement.objects.get(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    )
    assert movement.occurred_at == SCANNED_AT
    # created_at giữ nguyên nghĩa "ghi sổ lúc nào" → vẫn là hôm nay.
    assert movement.created_at > movement.occurred_at
    assert movement.occurred_at <= movement.created_at


def test_manual_match_stamps_scan_date(order, warehouse, staff_user):
    """Khớp tay cũng đi qua cùng đường ghi sổ → cùng luật ngày."""
    scan = _scan_backdated(
        warehouse=warehouse, staff_user=staff_user, kind=PendingScanKind.DISPATCH
    )

    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=staff_user
    )

    assert result["ok"] is True
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    movement = StockMovement.objects.get(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    )
    assert movement.occurred_at == SCANNED_AT


def test_return_match_stamps_scan_date_and_keeps_doc_date(order, warehouse, staff_user):
    """Nhánh hoàn: bút toán mang ngày quét, phiếu ngày vẫn đúng như trước."""
    _scan_backdated(
        warehouse=warehouse,
        staff_user=staff_user,
        kind=PendingScanKind.RETURN,
        return_type="delivery_returned",
    )

    PendingScanService.match_pending_scans()

    entry = ReturnEntry.objects.get(shipping_code="SDX1")
    # Hành vi đang ĐÚNG, không được hồi quy.
    assert entry.return_note.doc_date == SCANNED_LOCAL_DATE
    movement = StockMovement.objects.get(
        reference_type="return_entry",
        reference_id=entry.id,
        movement_type=MovementType.RETURN_IN,
    )
    assert movement.occurred_at == SCANNED_AT
    assert movement.created_at > movement.occurred_at


class TestInvoiceCodeMatching:
    """Quét XUẤT khớp thêm theo mã hoá đơn — đơn bán trực tiếp không có mã vận đơn."""

    def test_invoice_code_matches_and_deducts_at_order_warehouse(
        self, channel, warehouse, staff_user
    ):
        other_wh = Warehouse.objects.create(name="Kho quet", code="KT-SCAN")
        order = _make_order(channel, warehouse, "80", shipping_code="", qty=2)
        order.invoice_code = "HD8000"
        order.save(update_fields=["invoice_code"])
        item = order.items.first()
        before = _sellable_qty(warehouse.id, item.product_id)
        # Mã quét ở kho KHÁC kho của đơn: sổ phải ghi theo KHO CỦA ĐƠN.
        PendingShipmentScan.objects.create(
            code="HD8000", kind=PendingScanKind.DISPATCH,
            warehouse=other_wh, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="HD8000")
        assert scan.status == PendingScanStatus.MATCHED
        assert scan.matched_order_id == order.id
        assert _sellable_qty(warehouse.id, item.product_id) == before - item.quantity
        assert _sellable_qty(other_wh.id, item.product_id) == 0

    def test_shipping_code_wins_over_invoice_code(self, channel, warehouse, staff_user):
        order_a = _make_order(channel, warehouse, "81", shipping_code="DUP001", qty=1)
        order_b = _make_order(channel, warehouse, "82", shipping_code="", qty=1)
        order_b.invoice_code = "DUP001"
        order_b.save(update_fields=["invoice_code"])
        PendingShipmentScan.objects.create(
            code="DUP001", kind=PendingScanKind.DISPATCH,
            warehouse=warehouse, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="DUP001")
        assert scan.matched_order_id == order_a.id
        # order_a CŨ hơn order_b nhưng vẫn thắng — thông điệp phải nói đúng lý do
        # (ưu tiên mã vận đơn), không được nói "lấy đơn mới nhất" (sai bản chất và
        # đánh lạc hướng kho khi tra soát lệch tồn).
        assert "ưu tiên" in scan.error.lower()
        assert "mới nhất" not in scan.error.lower()

    def test_invoice_match_without_order_warehouse_fails_loudly(
        self, channel, warehouse, staff_user
    ):
        order = _make_order(channel, warehouse, "83", shipping_code="", qty=1)
        order.invoice_code = "HD8300"
        order.fulfillment_warehouse = None
        order.save(update_fields=["invoice_code", "fulfillment_warehouse"])
        item = order.items.first()
        before = _sellable_qty(warehouse.id, item.product_id)
        PendingShipmentScan.objects.create(
            code="HD8300", kind=PendingScanKind.DISPATCH,
            warehouse=warehouse, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="HD8300")
        assert scan.status == PendingScanStatus.FAILED
        assert "kho xuat" in scan.error.lower() or "kho xuất" in scan.error.lower()
        # Không trừ nhầm vào kho của màn quét.
        assert _sellable_qty(warehouse.id, item.product_id) == before

    def test_return_scan_does_not_match_invoice_code(self, channel, warehouse, staff_user):
        order = _make_order(channel, warehouse, "84", shipping_code="", qty=1)
        order.invoice_code = "HD8400"
        order.save(update_fields=["invoice_code"])
        PendingShipmentScan.objects.create(
            code="HD8400", kind=PendingScanKind.RETURN,
            warehouse=warehouse, scanned_by=staff_user,
        )

        PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="HD8400")
        assert scan.status == PendingScanStatus.PENDING
        assert scan.matched_order_id is None

    def test_shipping_code_path_still_uses_scan_warehouse(
        self, channel, warehouse, staff_user
    ):
        # Khoá hành vi cũ: đơn giao vận vẫn trừ ở kho của MÀN QUÉT, không phải kho đơn.
        order_wh = Warehouse.objects.create(name="Kho don cu", code="KT-OLD")
        order = _make_order(channel, order_wh, "85", shipping_code="SPX85", qty=1)
        item = order.items.first()
        StockCommandService.record_movement(
            warehouse_id=warehouse.id, product_id=item.product_id,
            qty_delta=5, movement_type=MovementType.RECEIPT,
        )
        before_scan_wh = _sellable_qty(warehouse.id, item.product_id)
        PendingShipmentScan.objects.create(
            code="SPX85", kind=PendingScanKind.DISPATCH,
            warehouse=warehouse, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        assert _sellable_qty(warehouse.id, item.product_id) == before_scan_wh - item.quantity
