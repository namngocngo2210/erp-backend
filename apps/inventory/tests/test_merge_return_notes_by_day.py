"""Lệnh gộp hồi tố phiếu trả cũ (1 phiếu ↔ 1 đơn) thành phiếu NGÀY.

Dữ liệu seed mô phỏng đúng thứ migration 0022_return_entry để lại trên prod: mỗi phiếu cũ là
1 phiếu ngày ĐÃ CHỐT chứa duy nhất 1 nhóm đơn, movement trỏ ("return_entry", id).
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.management.commands import merge_return_notes_by_day as cmd_module
from apps.inventory.models import (
    MovementType,
    ReturnEntry,
    ReturnNote,
    ReturnNoteLine,
    ReturnNoteStatus,
    ReturnStatus,
    ReturnType,
    StockItem,
    StockMovement,
    StockState,
)
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse

RETURN_ENTRY_REFERENCE = "return_entry"


@pytest.fixture
def two_warehouses_and_product(db):
    wh = Warehouse.objects.create(name="Kho HN", code="HN")
    wh2 = Warehouse.objects.create(name="Kho HCM", code="HCM")
    product = Product.objects.create(sku="SKU1", name="SP 1")
    return wh, wh2, product


def _legacy_note(wh, day, seq: int) -> ReturnNote:
    """Phiếu cũ sau 0022: mã THyyyymmdd-000n, đã chốt (có dấu closed_at)."""
    return ReturnNote.objects.create(
        code=f"TH{day:%Y%m%d}-{seq:04d}",
        warehouse=wh,
        doc_date=day,
        status=ReturnNoteStatus.CLOSED,
        closed_at=timezone.now(),
    )


def _daily_note(wh, day, *, suffix="", status=ReturnNoteStatus.OPEN) -> ReturnNote:
    """Phiếu ngày của hệ thống MỚI: PTH-YYYYMMDD-K{wh}[-02]."""
    return ReturnNote.objects.create(
        code=f"PTH-{day:%Y%m%d}-K{wh.id}{suffix}",
        warehouse=wh,
        doc_date=day,
        status=status,
        closed_at=timezone.now() if status == ReturnNoteStatus.CLOSED else None,
    )


def _entry(note, *, shipping_code="", status=ReturnStatus.POSTED) -> ReturnEntry:
    return ReturnEntry.objects.create(
        return_note=note, shipping_code=shipping_code, status=status
    )


def _line(entry, product, qty=2) -> ReturnNoteLine:
    return ReturnNoteLine.objects.create(
        entry=entry,
        product=product,
        quantity=qty,
        return_type=ReturnType.DELIVERY_RETURNED,
    )


def _post_movement(entry, wh, product, qty=2, sign=1):
    return StockCommandService.record_movement(
        warehouse_id=wh.id,
        product_id=product.id,
        qty_delta=sign * qty,
        movement_type=MovementType.RETURN_IN,
        state=StockState.SELLABLE,
        reference_type=RETURN_ENTRY_REFERENCE,
        reference_id=entry.id,
    )


def _posted_entry(note, wh, product, *, shipping_code="", qty=2) -> ReturnEntry:
    entry = _entry(note, shipping_code=shipping_code, status=ReturnStatus.POSTED)
    _line(entry, product, qty)
    _post_movement(entry, wh, product, qty)
    return entry


def _stock() -> list[tuple]:
    return sorted(
        StockItem.objects.values_list("warehouse_id", "product_id", "state", "quantity")
    )


def _movements_resolve() -> None:
    """Mọi movement trỏ return_entry phải còn resolve được về entry đang tồn tại."""
    entry_ids = set(ReturnEntry.objects.values_list("id", flat=True))
    refs = set(
        StockMovement.objects.filter(
            reference_type=RETURN_ENTRY_REFERENCE
        ).values_list("reference_id", flat=True)
    )
    assert refs <= entry_ids, f"movement mồ côi: {refs - entry_ids}"


def _merge(*args, **kwargs):
    """Chạy lệnh trong test: pytest-django đã bọc sẵn 1 transaction → mức cô lập không
    đặt được, phải mở cờ tường minh (prod chạy autocommit và sẽ CommandError nếu thiếu)."""
    call_command(
        "merge_return_notes_by_day", "--allow-read-committed", *args, **kwargs
    )


@pytest.mark.django_db
def test_merge_groups_legacy_notes_by_day_without_touching_stock(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    entries = [
        _posted_entry(
            _legacy_note(wh, today, i + 1), wh, product, shipping_code=f"SPX{i}"
        )
        for i in range(3)
    ]
    before = _stock()

    _merge()

    assert ReturnNote.objects.count() == 1
    note = ReturnNote.objects.get()
    assert note.code == f"PTH-{today:%Y%m%d}-K{wh.id}"
    assert note.entries.count() == 3
    assert set(note.entries.values_list("id", flat=True)) == {e.id for e in entries}
    assert _stock() == before
    _movements_resolve()


@pytest.mark.django_db
def test_existing_daily_note_is_kept_and_absorbs_legacy_notes(two_warehouses_and_product):
    """Kho vẫn quét giữa lúc deploy 0022 và lúc chạy gộp → phiếu PTH- có thật."""
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    daily = ReturnNote.objects.create(
        code=f"PTH-{today:%Y%m%d}-K{wh.id}",
        warehouse=wh,
        doc_date=today,
        status=ReturnNoteStatus.OPEN,
    )
    live = _posted_entry(daily, wh, product, shipping_code="LIVE")
    legacy = [
        _posted_entry(
            _legacy_note(wh, today, i + 1), wh, product, shipping_code=f"OLD{i}"
        )
        for i in range(2)
    ]
    before = _stock()

    _merge()

    assert ReturnNote.objects.count() == 1
    note = ReturnNote.objects.get()
    assert note.id == daily.id
    assert note.code == f"PTH-{today:%Y%m%d}-K{wh.id}"
    assert note.status == ReturnNoteStatus.OPEN
    assert set(note.entries.values_list("id", flat=True)) == {
        live.id,
        *(e.id for e in legacy),
    }
    assert (
        ReturnNote.objects.filter(
            doc_date=today, warehouse=wh, status=ReturnNoteStatus.OPEN
        ).count()
        == 1
    )
    assert _stock() == before
    _movements_resolve()


@pytest.mark.django_db
def test_warehouses_are_not_merged_together(two_warehouses_and_product):
    wh, wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    _posted_entry(_legacy_note(wh, today, 1), wh, product)
    _posted_entry(_legacy_note(wh, today, 2), wh, product)
    _posted_entry(_legacy_note(wh2, today, 3), wh2, product)

    _merge()

    assert ReturnNote.objects.count() == 2
    assert ReturnNote.objects.filter(warehouse=wh).get().entries.count() == 2
    assert ReturnNote.objects.filter(warehouse=wh2).get().entries.count() == 1
    codes = set(ReturnNote.objects.values_list("code", flat=True))
    assert codes == {
        f"PTH-{today:%Y%m%d}-K{wh.id}",
        f"PTH-{today:%Y%m%d}-K{wh2.id}",
    }


@pytest.mark.django_db
def test_days_are_not_merged_together(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    yesterday = today - timedelta(days=1)
    _posted_entry(_legacy_note(wh, today, 1), wh, product)
    _posted_entry(_legacy_note(wh, yesterday, 1), wh, product)
    _posted_entry(_legacy_note(wh, yesterday, 2), wh, product)

    _merge()

    assert ReturnNote.objects.count() == 2
    assert ReturnNote.objects.get(doc_date=today).entries.count() == 1
    assert ReturnNote.objects.get(doc_date=yesterday).entries.count() == 2


@pytest.mark.django_db
def test_mixed_entry_statuses_survive_with_their_movements(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    day = ReturnNoteService.today_vn() - timedelta(days=3)

    posted = _posted_entry(_legacy_note(wh, day, 1), wh, product, shipping_code="P1")

    cancelled_note = _legacy_note(wh, day, 2)
    cancelled = _posted_entry(cancelled_note, wh, product, shipping_code="C1")
    _post_movement(cancelled, wh, product, sign=-1)  # bút toán đảo khi huỷ
    ReturnEntry.objects.filter(id=cancelled.id).update(status=ReturnStatus.CANCELLED)

    draft_note = _legacy_note(wh, day, 3)
    draft = _entry(draft_note, shipping_code="D1", status=ReturnStatus.DRAFT)

    before = _stock()
    movements_before = {
        entry.id: set(
            StockMovement.objects.filter(
                reference_type=RETURN_ENTRY_REFERENCE, reference_id=entry.id
            ).values_list("id", flat=True)
        )
        for entry in (posted, cancelled)
    }

    _merge()

    assert ReturnNote.objects.count() == 1
    note = ReturnNote.objects.get()
    assert note.entries.count() == 3
    assert ReturnEntry.objects.get(id=posted.id).status == ReturnStatus.POSTED
    assert ReturnEntry.objects.get(id=cancelled.id).status == ReturnStatus.CANCELLED
    assert ReturnEntry.objects.get(id=draft.id).status == ReturnStatus.DRAFT
    assert ReturnNoteLine.objects.filter(entry_id=posted.id).count() == 1
    for entry_id, movement_ids in movements_before.items():
        assert (
            set(
                StockMovement.objects.filter(
                    reference_type=RETURN_ENTRY_REFERENCE, reference_id=entry_id
                ).values_list("id", flat=True)
            )
            == movement_ids
        )
    assert _stock() == before
    _movements_resolve()


@pytest.mark.django_db
def test_dry_run_changes_nothing(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for i in range(2):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)
    codes = set(ReturnNote.objects.values_list("code", flat=True))

    _merge("--dry-run")

    assert ReturnNote.objects.count() == 2
    assert set(ReturnNote.objects.values_list("code", flat=True)) == codes
    assert ReturnEntry.objects.count() == 2


@pytest.mark.django_db
def test_date_from_limits_scope(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    old = today - timedelta(days=10)
    for i in range(2):
        _posted_entry(_legacy_note(wh, old, i + 1), wh, product)
    for i in range(2):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)

    _merge("--date-from", (today - timedelta(days=1)).isoformat())

    # Ngày cũ nằm ngoài phạm vi → giữ nguyên 2 phiếu cũ; hôm nay gộp còn 1.
    assert ReturnNote.objects.filter(doc_date=old).count() == 2
    assert ReturnNote.objects.filter(doc_date=today).count() == 1
    assert ReturnNote.objects.get(doc_date=today).code == f"PTH-{today:%Y%m%d}-K{wh.id}"


@pytest.mark.django_db
def test_today_stays_open_and_past_day_is_closed(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    past = today - timedelta(days=2)
    for i in range(2):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)
    for i in range(2):
        _posted_entry(_legacy_note(wh, past, i + 1), wh, product)

    _merge()

    open_note = ReturnNote.objects.get(doc_date=today)
    closed_note = ReturnNote.objects.get(doc_date=past)
    assert open_note.status == ReturnNoteStatus.OPEN
    assert closed_note.status == ReturnNoteStatus.CLOSED
    assert ReturnNote.objects.filter(status=ReturnNoteStatus.OPEN).count() == 1
    # closed_at phải khớp status: phiếu mở không được mang "chốt lúc ..." kế thừa từ
    # phiếu cũ; phiếu chốt phải có mốc thời gian chốt.
    assert open_note.closed_at is None
    assert open_note.closed_by_id is None
    assert closed_note.closed_at is not None


@pytest.mark.django_db
def test_no_note_is_deleted_while_it_still_has_entries(two_warehouses_and_product):
    """Guard CASCADE: xoá phiếu còn entry sẽ xoá luôn entry + đứt audit trail."""
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for i in range(3):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)
    entry_ids = set(ReturnEntry.objects.values_list("id", flat=True))
    line_count = ReturnNoteLine.objects.count()

    _merge()

    assert set(ReturnEntry.objects.values_list("id", flat=True)) == entry_ids
    assert ReturnNoteLine.objects.count() == line_count


@pytest.mark.django_db
def test_closed_note_gets_a_closed_at_even_if_legacy_had_none(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    past = ReturnNoteService.today_vn() - timedelta(days=2)
    for i in range(2):
        note = _legacy_note(wh, past, i + 1)
        ReturnNote.objects.filter(id=note.id).update(closed_at=None)
        _posted_entry(note, wh, product)

    _merge()

    note = ReturnNote.objects.get(doc_date=past)
    assert note.status == ReturnNoteStatus.CLOSED
    assert note.closed_at is not None  # fallback: created_at của chính phiếu


@pytest.mark.django_db
def test_new_system_split_notes_are_left_alone(two_warehouses_and_product):
    """Ngày chỉ có phiếu PTH- (kể cả -02 do kho chủ động tách) → lệnh KHÔNG đụng vào.

    Mã PTH- là chứng từ người dùng đã thấy và -02 là ranh giới kế toán cố ý.
    """
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    base = _daily_note(wh, today, status=ReturnNoteStatus.CLOSED)
    split = _daily_note(wh, today, suffix="-02", status=ReturnNoteStatus.OPEN)
    _posted_entry(base, wh, product, shipping_code="A")
    _posted_entry(split, wh, product, shipping_code="B")
    before = _stock()

    _merge()

    base.refresh_from_db()
    split.refresh_from_db()
    assert ReturnNote.objects.count() == 2
    assert base.code == f"PTH-{today:%Y%m%d}-K{wh.id}"
    assert base.status == ReturnNoteStatus.CLOSED
    assert split.code == f"PTH-{today:%Y%m%d}-K{wh.id}-02"
    assert split.status == ReturnNoteStatus.OPEN
    assert base.entries.count() == 1
    assert split.entries.count() == 1
    assert _stock() == before


@pytest.mark.django_db
def test_legacy_entries_land_in_open_split_note_without_renaming_anything(two_warehouses_and_product):
    """Cùng ngày đó nhưng CÓ phiếu cũ: entry cũ dồn vào phiếu PTH- đang MỞ (-02).

    Chọn phiếu MỞ vì đó là phiếu kho đang quét vào; dồn vào phiếu đã chốt (base) sẽ
    mở lại một ranh giới kế toán mà kho đã cố ý đóng. Không phiếu PTH- nào bị đổi mã.
    """
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    base = _daily_note(wh, today, status=ReturnNoteStatus.CLOSED)
    split = _daily_note(wh, today, suffix="-02", status=ReturnNoteStatus.OPEN)
    _posted_entry(base, wh, product, shipping_code="A")
    _posted_entry(split, wh, product, shipping_code="B")
    legacy_note = _legacy_note(wh, today, 1)
    legacy_entry = _posted_entry(legacy_note, wh, product, shipping_code="OLD")
    before = _stock()

    _merge()

    base.refresh_from_db()
    split.refresh_from_db()
    assert ReturnNote.objects.count() == 2  # phiếu cũ bị xoá sau khi rỗng
    assert not ReturnNote.objects.filter(id=legacy_note.id).exists()
    assert base.code == f"PTH-{today:%Y%m%d}-K{wh.id}"
    assert split.code == f"PTH-{today:%Y%m%d}-K{wh.id}-02"
    assert base.status == ReturnNoteStatus.CLOSED
    assert split.status == ReturnNoteStatus.OPEN
    assert ReturnEntry.objects.get(id=legacy_entry.id).return_note_id == split.id
    assert split.entries.count() == 2
    assert base.entries.count() == 1
    assert _stock() == before
    _movements_resolve()


@pytest.mark.django_db
def test_cascade_guard_blocks_delete_when_rehome_silently_fails(monkeypatch, two_warehouses_and_product):
    """Phá hoại có chủ đích: re-home thành no-op → guard phải chặn và rollback.

    Không có guard, DELETE phiếu còn entry sẽ CASCADE xoá cả entry đã ghi sổ.
    """
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for i in range(3):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)
    note_ids = set(ReturnNote.objects.values_list("id", flat=True))
    entry_ids = set(ReturnEntry.objects.values_list("id", flat=True))
    line_count = ReturnNoteLine.objects.count()

    monkeypatch.setattr(cmd_module, "_rehome_entries", lambda drop_ids, keep_id: 0)

    with pytest.raises(CommandError, match="CASCADE"):
        _merge()

    assert set(ReturnNote.objects.values_list("id", flat=True)) == note_ids
    assert set(ReturnEntry.objects.values_list("id", flat=True)) == entry_ids
    assert ReturnNoteLine.objects.count() == line_count
    _movements_resolve()


@pytest.mark.django_db(transaction=True)
def test_real_run_uses_repeatable_read(two_warehouses_and_product):
    """Chạy như prod (autocommit, không bị pytest bọc transaction) → phải là
    REPEATABLE READ, nếu không lệnh tự dừng."""
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for i in range(2):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)
    out = StringIO()

    call_command("merge_return_notes_by_day", stdout=out)

    assert "Mức cô lập: repeatable read" in out.getvalue()
    assert ReturnNote.objects.count() == 1
    assert ReturnNote.objects.get().entries.count() == 2


@pytest.mark.django_db
def test_rerun_is_idempotent(two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for i in range(2):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)

    _merge()
    before = _stock()
    _merge()

    assert ReturnNote.objects.count() == 1
    assert ReturnNote.objects.get().entries.count() == 2
    assert _stock() == before


@pytest.mark.django_db
def test_lock_budget_guard_blocks_run_when_day_keys_exceed_budget(monkeypatch, two_warehouses_and_product):
    """Mỗi (ngày, kho) = 1 advisory lock session; vượt bảng khoá chung → chết giữa chừng.

    Guard phải chặn TRƯỚC khi lấy khoá nào, và chỉ đường cắt lát bằng --date-from.
    """
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for offset in range(3):
        day = today - timedelta(days=offset)
        _posted_entry(_legacy_note(wh, day, 1), wh, product)
    codes = set(ReturnNote.objects.values_list("code", flat=True))

    # ngân sách bé xíu → limit = int(4 * 0.20) = 0 → 3 khoá là vượt.
    monkeypatch.setattr(cmd_module, "_advisory_lock_budget", lambda: 4)

    with pytest.raises(CommandError) as exc:
        _merge()

    assert "advisory lock" in str(exc.value)
    assert "--date-from" in str(exc.value)  # phải chỉ đường cắt lát
    # Không ghi gì, không đổi mã phiếu nào.
    assert set(ReturnNote.objects.values_list("code", flat=True)) == codes


@pytest.mark.django_db
def test_force_bypasses_lock_budget_guard(monkeypatch, two_warehouses_and_product):
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for i in range(2):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)

    monkeypatch.setattr(cmd_module, "_advisory_lock_budget", lambda: 4)

    _merge("--force")

    assert ReturnNote.objects.count() == 1
    assert ReturnNote.objects.get().code == f"PTH-{today:%Y%m%d}-K{wh.id}"
    assert ReturnNote.objects.get().entries.count() == 2


@pytest.mark.django_db
def test_running_inside_an_open_transaction_is_refused(two_warehouses_and_product):
    """Celery task / ATOMIC_REQUESTS / shell đang mở tx → không đặt được REPEATABLE READ.

    Không được âm thầm tụt về READ COMMITTED (đối soát tồn sẽ thành báo động giả) —
    phải dừng hẳn. pytest-django bọc sẵn transaction nên test này = đúng tình huống đó.
    """
    wh, _wh2, product = two_warehouses_and_product
    today = ReturnNoteService.today_vn()
    for i in range(2):
        _posted_entry(_legacy_note(wh, today, i + 1), wh, product)

    with pytest.raises(CommandError, match="transaction"):
        call_command("merge_return_notes_by_day")

    assert ReturnNote.objects.count() == 2  # không đụng gì


@pytest.mark.django_db
def test_month_slices_are_suggested_newest_first(two_warehouses_and_product):
    from datetime import date as _date

    keys = [
        (_date(2026, 1, 5), 1),
        (_date(2026, 1, 20), 2),
        (_date(2026, 3, 2), 1),
        (_date(2025, 12, 31), 1),
    ]
    assert cmd_module._month_slices(keys) == [
        _date(2026, 3, 1),
        _date(2026, 1, 1),
        _date(2025, 12, 1),
    ]
