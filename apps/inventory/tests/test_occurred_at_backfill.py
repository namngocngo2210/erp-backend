"""Nắn ngày nghiệp vụ cho bút toán đã ghi sai trước khi có occurred_at."""
from datetime import datetime, timedelta, UTC

import pytest
from django.db import connection

from apps.customers.models import Customer
from apps.inventory.application.services import occurred_at_backfill
from apps.inventory.application.services.occurred_at_backfill import (
    backfill_from_pending_scans,
)
from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.application.services.stock_sales_service import (
    REVIVE_REISSUE_NOTE,
)
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnEntry,
    ReturnNote,
    ReturnStatus,
    StockMovement,
)
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

SCANNED_AT = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)
POSTED_AT = datetime(2026, 7, 6, 3, 0, tzinfo=UTC)
# Lần quét THỨ HAI trên cùng một mục tiêu (đơn bị huỷ rồi quét lại): mốc thời
# gian phải nằm SAU lần ghi sổ đầu tiên để dựng đúng dòng thời gian thật.
SCANNED_AT_2 = datetime(2026, 7, 8, 3, 0, tzinfo=UTC)
POSTED_AT_2 = datetime(2026, 7, 10, 3, 0, tzinfo=UTC)


def _make_order(channel, warehouse, suffix):
    """`matched_order` trên PendingShipmentScan là FK thật (khác reference_id
    trên StockMovement, chỉ là BigIntegerField) nên cần một Order thật tồn tại
    — nếu không, kiểm tra ràng buộc khoá ngoại hoãn lại (deferred) sẽ nổ lúc
    Django dọn transaction test, sau khi assertion đã chạy xong."""
    customer = Customer.objects.create(
        full_name=f"Khach Backfill {suffix}",
        phone_raw=f"09{suffix:08d}",
        phone_normalized=f"09{suffix:08d}",
    )
    return Order.objects.create(
        channel=channel,
        customer=customer,
        name=f"Khach Backfill {suffix}",
        fulfillment_warehouse=warehouse,
    )


def _movement(
    warehouse, product, *, movement_type, reference_type, reference_id,
    note="", posted_at=POSTED_AT,
):
    """Dựng bút toán "đời cũ": occurred_at == created_at == ngày cron chạy."""
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-1 if movement_type == MovementType.SALE else 1,
        movement_type=movement_type,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
    )
    StockMovement.objects.filter(id=result.movement_id).update(
        created_at=posted_at, occurred_at=posted_at
    )
    return StockMovement.objects.get(id=result.movement_id)


def test_backfill_moves_dispatch_sale_to_scan_time(warehouse, product, channel):
    order_id = _make_order(channel, warehouse, 90001).id
    sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
    )
    scan = PendingShipmentScan.objects.create(
        code="BF1", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, matched_order_id=order_id
    )

    stats = backfill_from_pending_scans(connection)

    sale.refresh_from_db()
    assert stats["dispatch"] == 1
    assert sale.occurred_at == SCANNED_AT
    # created_at là partition key — tuyệt đối không đụng.
    assert sale.created_at == POSTED_AT


def test_backfill_leaves_sale_reversal_alone(warehouse, product, channel):
    order_id = _make_order(channel, warehouse, 90002).id
    reversal = _movement(
        warehouse, product,
        movement_type=MovementType.SALE_REVERSAL,
        reference_type="order", reference_id=order_id,
    )
    scan = PendingShipmentScan.objects.create(
        code="BF2", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, matched_order_id=order_id
    )

    backfill_from_pending_scans(connection)

    reversal.refresh_from_db()
    assert reversal.occurred_at == POSTED_AT


def _dispatch_scan(warehouse, order_id, *, code, scanned_at=SCANNED_AT):
    scan = PendingShipmentScan.objects.create(
        code=code, kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=scanned_at, matched_order_id=order_id
    )
    return scan


def test_backfill_leaves_admin_sale_alone(warehouse, product, channel):
    """SALE HÀNH CHÍNH (hồi sinh đơn huỷ / resync / chuyển sổ sang đơn em) dùng
    CÙNG movement_type=sale, cùng reference_type/reference_id với lần xuất thật
    — chỉ `note` khác rỗng phân biệt được. Nắn nhầm sẽ cộng dồn 2 lần trừ kho
    vào đúng ngày quét dù hàng chỉ rời kho một lần."""
    order_id = _make_order(channel, warehouse, 90004).id
    real_sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
    )
    admin_sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
        note=REVIVE_REISSUE_NOTE, posted_at=POSTED_AT_2,
    )
    _dispatch_scan(warehouse, order_id, code="BF4")

    stats = backfill_from_pending_scans(connection)

    real_sale.refresh_from_db()
    admin_sale.refresh_from_db()
    assert stats["dispatch"] == 1  # chỉ lần xuất thật được nắn
    assert real_sale.occurred_at == SCANNED_AT
    assert admin_sale.occurred_at == POSTED_AT_2  # giữ nguyên ngày hồi sinh thật


def test_backfill_pairs_each_dispatch_sale_with_its_own_scan(warehouse, product, channel):
    """Đơn bị huỷ rồi quét & xuất lại có HAI dòng SALE thật trên cùng đơn. Mỗi
    dòng phải về đúng lần quét sinh ra nó (lần quét gần nhất không sau ngày ghi
    sổ của chính nó), không phải cùng dồn về lần quét sớm nhất."""
    order_id = _make_order(channel, warehouse, 90005).id
    first_sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
    )
    second_sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
        posted_at=POSTED_AT_2,
    )
    _dispatch_scan(warehouse, order_id, code="BF5A", scanned_at=SCANNED_AT)
    _dispatch_scan(warehouse, order_id, code="BF5B", scanned_at=SCANNED_AT_2)

    stats = backfill_from_pending_scans(connection)

    first_sale.refresh_from_db()
    second_sale.refresh_from_db()
    assert stats["dispatch"] == 2
    assert first_sale.occurred_at == SCANNED_AT
    assert second_sale.occurred_at == SCANNED_AT_2  # KHÔNG phải SCANNED_AT
    assert second_sale.occurred_at <= second_sale.created_at  # bất biến hệ thống


def test_backfill_is_idempotent(warehouse, product, channel):
    order_id = _make_order(channel, warehouse, 90003).id
    sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
    )
    _dispatch_scan(warehouse, order_id, code="BF3")
    entry = _return_entry(warehouse, shipping_code="BFRET-IDEM", suffix=90003)
    original = _return_movement(warehouse, product, entry, qty_delta=2, note="")
    _return_scan(warehouse, code="BFRET-IDEM", suffix=90003)

    first = backfill_from_pending_scans(connection)
    second = backfill_from_pending_scans(connection)

    sale.refresh_from_db()
    original.refresh_from_db()
    assert first == {"dispatch": 1, "return": 1}
    # Lần hai không còn gì để nắn ở CẢ HAI nhánh: chạy lại migration vô hại.
    assert second["dispatch"] == 0
    assert second["return"] == 0
    assert sale.occurred_at == SCANNED_AT
    assert original.occurred_at == SCANNED_AT


def _return_entry(warehouse, *, shipping_code, suffix, created_at=POSTED_AT):
    """Dựng ReturnEntry đã ghi sổ, đủ để làm reference cho StockMovement.

    `created_at` ghi đè được vì nhánh return ghép mỗi nhóm đơn với lần quét gần
    nhất KHÔNG sau ngày tạo nhóm đơn — muốn dựng cảnh "quét lại lần 2" thì phải
    đặt được ngày tạo của từng nhóm đơn.
    """
    note_obj = ReturnNote.objects.create(
        code=f"PT-BF-{suffix}", warehouse=warehouse, doc_date=created_at.date()
    )
    entry = ReturnEntry.objects.create(
        return_note=note_obj, shipping_code=shipping_code, status=ReturnStatus.POSTED
    )
    ReturnEntry.objects.filter(pk=entry.pk).update(created_at=created_at)
    return ReturnEntry.objects.get(pk=entry.pk)


def _return_movement(warehouse, product, entry, *, qty_delta, note="", posted_at=POSTED_AT):
    """Bút toán "đời cũ" trên nhóm đơn hoàn — `note` phân biệt gốc/đảo/điều chỉnh
    y hệt cách return_entry_service/return_entry_adjust_service ghi thật."""
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=qty_delta,
        movement_type=MovementType.RETURN_IN,
        reference_type="return_entry",
        reference_id=entry.id,
        note=note,
    )
    StockMovement.objects.filter(id=result.movement_id).update(
        created_at=posted_at, occurred_at=posted_at
    )
    return StockMovement.objects.get(id=result.movement_id)


def _return_scan(warehouse, *, code, suffix, scanned_at=SCANNED_AT):
    scan = PendingShipmentScan.objects.create(
        code=code, kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(created_at=scanned_at)
    return scan


def test_backfill_moves_return_original_posting_to_scan_time(warehouse, product):
    entry = _return_entry(warehouse, shipping_code="BFRET1", suffix=90101)
    original = _return_movement(warehouse, product, entry, qty_delta=2, note="")
    _return_scan(warehouse, code="BFRET1", suffix=90101)

    stats = backfill_from_pending_scans(connection)

    original.refresh_from_db()
    assert stats["return"] == 1
    assert original.occurred_at == SCANNED_AT
    # created_at là partition key — tuyệt đối không đụng.
    assert original.created_at == POSTED_AT


def test_backfill_leaves_return_cancellation_reversal_alone(warehouse, product):
    """cancel_entry đảo bằng CÙNG movement_type/reference — chỉ note khác rỗng
    và qty_delta âm phân biệt được với bút toán gốc. Nắn nhầm sẽ dồn cả +qty và
    -qty về ngày quét, xoá mất ngày huỷ thật khỏi sổ."""
    entry = _return_entry(warehouse, shipping_code="BFRET2", suffix=90102)
    original = _return_movement(warehouse, product, entry, qty_delta=2, note="")
    reversal = _return_movement(
        warehouse, product, entry,
        qty_delta=-2,
        note=f"Huỷ nhóm đơn #{entry.id} trong phiếu {entry.return_note.code}",
    )
    _return_scan(warehouse, code="BFRET2", suffix=90102)

    stats = backfill_from_pending_scans(connection)

    original.refresh_from_db()
    reversal.refresh_from_db()
    assert stats["return"] == 1  # chỉ bút toán gốc được nắn
    assert original.occurred_at == SCANNED_AT
    assert reversal.occurred_at == POSTED_AT  # bút toán đảo giữ nguyên ngày huỷ thật


def test_backfill_leaves_return_adjustment_alone(warehouse, product):
    """return_entry_adjust_service ghi note khác rỗng dù qty_delta có thể DƯƠNG
    (tăng số lượng) — cố ý dùng delta dương ở đây để chứng minh riêng điều kiện
    qty_delta > 0 không đủ; phải có cả note = '' mới chặn được."""
    entry = _return_entry(warehouse, shipping_code="BFRET3", suffix=90103)
    original = _return_movement(warehouse, product, entry, qty_delta=2, note="")
    adjustment = _return_movement(
        warehouse, product, entry,
        qty_delta=1,
        note=f"Điều chỉnh nhóm đơn #{entry.id} trong phiếu {entry.return_note.code}",
    )
    _return_scan(warehouse, code="BFRET3", suffix=90103)

    stats = backfill_from_pending_scans(connection)

    original.refresh_from_db()
    adjustment.refresh_from_db()
    assert stats["return"] == 1  # chỉ bút toán gốc được nắn
    assert original.occurred_at == SCANNED_AT
    assert adjustment.occurred_at == POSTED_AT  # điều chỉnh giữ nguyên ngày điều chỉnh thật


def test_backfill_leaves_negative_return_movement_alone(warehouse, product):
    """Chốt riêng điều kiện `qty_delta > 0`: dòng ÂM mà note RỖNG vẫn không được
    nắn. Nếu ai đó bỏ điều kiện này thì test note-khác-rỗng ở trên vẫn xanh —
    phải có case này mới phát hiện được."""
    entry = _return_entry(warehouse, shipping_code="BFRET4", suffix=90104)
    original = _return_movement(warehouse, product, entry, qty_delta=2, note="")
    negative = _return_movement(warehouse, product, entry, qty_delta=-2, note="")
    _return_scan(warehouse, code="BFRET4", suffix=90104)

    stats = backfill_from_pending_scans(connection)

    original.refresh_from_db()
    negative.refresh_from_db()
    assert stats["return"] == 1  # chỉ dòng dương được nắn
    assert original.occurred_at == SCANNED_AT
    assert negative.occurred_at == POSTED_AT


def test_backfill_ignores_empty_scan_code(warehouse, product):
    """`code = ''` là mã quét hỏng/chưa nhập. Không chặn thì nó khớp với MỌI
    ReturnEntry cũng có shipping_code rỗng và kéo cả cụm về sai ngày."""
    entry = _return_entry(warehouse, shipping_code="", suffix=90105)
    original = _return_movement(warehouse, product, entry, qty_delta=2, note="")
    _return_scan(warehouse, code="", suffix=90105)

    stats = backfill_from_pending_scans(connection)

    original.refresh_from_db()
    assert stats["return"] == 0
    assert original.occurred_at == POSTED_AT


def test_backfill_pairs_each_return_entry_with_its_own_scan(warehouse, product):
    """Huỷ-rồi-quét-lại sinh HAI ReturnEntry dùng chung một shipping_code (ràng
    buộc unique chỉ ép trên entry draft không có source_order). Mỗi nhóm đơn
    phải về đúng lần quét sinh ra nó, không phải cùng dồn về lần quét sớm nhất
    — dồn sai chính là lỗi mà migration này sinh ra để sửa."""
    first_entry = _return_entry(
        warehouse, shipping_code="BFRET5", suffix=90106, created_at=POSTED_AT
    )
    first_move = _return_movement(warehouse, product, first_entry, qty_delta=2, note="")
    second_entry = _return_entry(
        warehouse, shipping_code="BFRET5", suffix=90107, created_at=POSTED_AT_2
    )
    second_move = _return_movement(
        warehouse, product, second_entry, qty_delta=2, note="", posted_at=POSTED_AT_2
    )
    _return_scan(warehouse, code="BFRET5", suffix=90106, scanned_at=SCANNED_AT)
    _return_scan(warehouse, code="BFRET5", suffix=90107, scanned_at=SCANNED_AT_2)

    stats = backfill_from_pending_scans(connection)

    first_move.refresh_from_db()
    second_move.refresh_from_db()
    assert stats["return"] == 2
    assert first_move.occurred_at == SCANNED_AT
    assert second_move.occurred_at == SCANNED_AT_2  # KHÔNG phải SCANNED_AT
    assert second_move.occurred_at <= second_move.created_at  # bất biến hệ thống


def test_backfill_batches_id_range_without_double_counting(
    monkeypatch, warehouse, product, channel
):
    """`_run_batched` chia dải id `stock_movements` thành nhiều lô theo `_BATCH`.
    Mọi test khác trong file này tạo dưới 10 bút toán nên vòng lặp luôn chạy
    đúng MỘT lượt — một lỗi lệch biên (bỏ sót hay xử lý trùng một dòng ở ranh
    giới lô) sẽ vô hình với các test đó. Module rewrite dữ liệu tài chính lịch
    sử nên khoảng trống này quan trọng.

    Ép `_BATCH = 1` để mỗi bút toán rơi vào MỘT lô riêng, buộc vòng lặp chạy
    nhiều lượt thật sự, rồi so kết quả với một nhóm dữ liệu CÙNG HÌNH DẠNG chạy
    ở `_BATCH` mặc định — số dòng nắn phải bằng nhau (không đếm thiếu, không
    đếm thừa)."""

    def _build_group(suffix):
        order_id = _make_order(channel, warehouse, suffix).id
        real_sale = _movement(
            warehouse, product,
            movement_type=MovementType.SALE,
            reference_type="order", reference_id=order_id,
        )
        admin_sale = _movement(
            warehouse, product,
            movement_type=MovementType.SALE,
            reference_type="order", reference_id=order_id,
            note=REVIVE_REISSUE_NOTE, posted_at=POSTED_AT_2,
        )
        _dispatch_scan(warehouse, order_id, code=f"BFBATCH{suffix}")
        # `returnnote_open_day_uniq` chặn hai phiếu OPEN cùng ngày/cùng kho —
        # mỗi nhóm phải có doc_date riêng, không ảnh hưởng occurred_at kỳ vọng
        # vì đó vẫn là ngày quét (SCANNED_AT), không phải ngày tạo phiếu.
        entry = _return_entry(
            warehouse, shipping_code=f"BFBATCHRET{suffix}", suffix=suffix,
            created_at=POSTED_AT + timedelta(days=suffix - 90201),
        )
        original = _return_movement(warehouse, product, entry, qty_delta=2, note="")
        negative = _return_movement(warehouse, product, entry, qty_delta=-2, note="")
        _return_scan(warehouse, code=f"BFBATCHRET{suffix}", suffix=suffix)
        return real_sale, admin_sale, original, negative

    # Nhóm A chạy với _BATCH mặc định (một lượt duy nhất) — làm chuẩn so sánh.
    a_sale, a_admin, a_original, a_negative = _build_group(90201)
    baseline = backfill_from_pending_scans(connection)
    assert baseline == {"dispatch": 1, "return": 1}

    # Nhóm B giống hệt hình dạng nhóm A nhưng dựng SAU khi ép _BATCH = 1. Lượt
    # chạy backfill thứ hai quét lại toàn bộ dải id — gồm cả nhóm A (giờ là
    # no-op vì đã nắn xong) lẫn nhóm B (còn cần nắn) — theo từng lô 1 dòng.
    monkeypatch.setattr(occurred_at_backfill, "_BATCH", 1)
    b_sale, b_admin, b_original, b_negative = _build_group(90202)
    batched = backfill_from_pending_scans(connection)

    assert batched == baseline == {"dispatch": 1, "return": 1}

    for movement in (a_sale, a_admin, a_original, a_negative,
                     b_sale, b_admin, b_original, b_negative):
        movement.refresh_from_db()

    assert a_sale.occurred_at == SCANNED_AT
    assert a_original.occurred_at == SCANNED_AT
    assert a_admin.occurred_at == POSTED_AT_2  # bút toán hành chính: giữ nguyên
    assert a_negative.occurred_at == POSTED_AT  # dòng âm: giữ nguyên

    assert b_sale.occurred_at == SCANNED_AT
    assert b_original.occurred_at == SCANNED_AT
    assert b_admin.occurred_at == POSTED_AT_2
    assert b_negative.occurred_at == POSTED_AT
