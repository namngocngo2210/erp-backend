from __future__ import annotations

from datetime import date

from django.db import connection, transaction
from django.utils import timezone

from apps.inventory.models import ReturnNote, ReturnNoteStatus

# Advisory lock class riêng cho phiếu ngày — 2 người quét cùng lúc không đẻ 2 phiếu.
NOTE_LOCK_CLASS = 62001


def day_lock_key(doc_date: date, warehouse_id: int) -> int:
    return (int(doc_date.strftime("%Y%m%d")) * 1000 + warehouse_id) & 0x7FFFFFFF


def lock_day(doc_date: date, warehouse_id: int) -> None:
    """Khoá (ngày, kho) trong phạm vi TRANSACTION — tự nhả khi commit/rollback."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s);",
            [NOTE_LOCK_CLASS, day_lock_key(doc_date, warehouse_id)],
        )


def lock_day_session(doc_date: date, warehouse_id: int) -> None:
    """Khoá (ngày, kho) ở phạm vi SESSION — sống qua nhiều transaction.

    Dùng cho job hàng loạt cần giữ khoá TRƯỚC khi mở transaction (xem
    `merge_return_notes_by_day`). Cùng lock class với `lock_day` nên luồng quét
    xếp hàng sau. Nhớ gọi `unlock_all_day_locks()` trong `finally`.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_lock(%s, %s);",
            [NOTE_LOCK_CLASS, day_lock_key(doc_date, warehouse_id)],
        )


def unlock_all_day_locks() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock_all();")


class ReturnNoteService:
    """Nghiệp vụ của PHIẾU NGÀY: mở phiếu + sinh mã. Ngữ nghĩa theo đơn nằm ở
    ReturnEntryService."""

    @staticmethod
    def today_vn() -> date:
        """Ngày chứng từ theo giờ VN — quét lúc 23h30 VN vẫn thuộc ngày hôm nay."""
        return timezone.localdate()

    @staticmethod
    def _next_code(doc_date: date, warehouse_id: int) -> str:
        base = f"PTH-{doc_date:%Y%m%d}-K{warehouse_id}"
        if not ReturnNote.objects.filter(code=base).exists():
            return base
        # Phiếu trước đã chốt → phiếu tiếp theo trong ngày: -02, -03, ...
        seq = ReturnNote.objects.filter(code__startswith=f"{base}-").count() + 2
        return f"{base}-{seq:02d}"

    @staticmethod
    @transaction.atomic
    def resolve_open_note(
        *, warehouse_id: int, doc_date: date, user_id: int | None = None
    ) -> ReturnNote:
        """Phiếu ngày ĐANG MỞ của (ngày, kho); tạo mới nếu chưa có."""
        lock_day(doc_date, warehouse_id)
        note = ReturnNote.objects.filter(
            warehouse_id=warehouse_id,
            doc_date=doc_date,
            status=ReturnNoteStatus.OPEN,
        ).first()
        if note is not None:
            return note
        return ReturnNote.objects.create(
            code=ReturnNoteService._next_code(doc_date, warehouse_id),
            warehouse_id=warehouse_id,
            doc_date=doc_date,
            status=ReturnNoteStatus.OPEN,
            created_by_id=user_id,
        )

    # close_note / cancel_note (chốt & huỷ CẢ phiếu ngày) đã gỡ 2026-07-14 cùng UI
    # phiếu trả: phiếu ngày giờ chỉ là sổ gom ngầm, không ai chốt/huỷ tay nữa —
    # sửa sai đi theo từng nhóm đơn (ReturnEntryService.cancel_entry).
