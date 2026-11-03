"""Ghi chú tự do của các dòng trên trang Lịch sử nhập xuất.

Mỗi dòng của trang là một loại thực thể khác nhau. Ba loại đã có field `note`
riêng từ trước (mã quét chờ khớp, nhóm đơn hoàn, phiếu kho) — service này
KHÔNG sao chép chúng sang chỗ mới, chỉ điều hướng lệnh ghi về đúng nguồn. Hai
loại còn lại (dòng đơn xuất, dòng chờ hàng hoàn về) không có thực thể nào để
gắn nên lưu ở bảng `stock_flow_notes`.

Nhờ đó cột "Ghi chú" trên màn hình chỉ có MỘT nguồn sự thật cho mỗi loại dòng,
không đẻ ra bản sao lệch nhau.

Thiết kế: docs/flow-history-notes.md
"""
from __future__ import annotations

from apps.inventory.models import (
    ReturnEntry,
    StockDocument,
    StockFlowNote,
    StockFlowNoteTarget,
)

# Hai loại dòng lưu ở bảng riêng — cũng là hai giá trị hợp lệ của
# `StockFlowNote.target_type`.
TABLE_TARGETS = frozenset(StockFlowNoteTarget.values)

# Ba loại dòng ghi thẳng vào field sẵn có của thực thể.
ENTITY_TARGETS = frozenset({"stock_document", "return_entry", "pending_scan"})

VALID_TARGETS = TABLE_TARGETS | ENTITY_TARGETS

_UNKNOWN_TARGET = "target_type không hợp lệ."
_NOT_FOUND = "Không tìm thấy dòng cần ghi chú."


class FlowNoteService:
    """Đọc/ghi ghi chú cho mọi loại dòng của trang Lịch sử nhập xuất."""

    @staticmethod
    def notes_for(target_type: str, target_ids: list[int]) -> dict[int, str]:
        """{target_id: note} cho MỘT trang danh sách — đúng một query.

        Chỉ phục vụ hai loại lưu ở `stock_flow_notes`; ba loại còn lại đã mang
        sẵn note trong hàng dữ liệu của chúng.
        """
        if not target_ids:
            return {}
        rows = StockFlowNote.objects.filter(
            target_type=target_type, target_id__in=target_ids
        ).values_list("target_id", "note")
        return {tid: note or "" for tid, note in rows}

    @staticmethod
    def update(
        *, target_type: str, target_id: int, note: str, user_id: int | None
    ) -> dict:
        """Ghi ghi chú vào đúng nguồn của loại dòng.

        Ném ``ValueError`` khi `target_type` lạ, ``LookupError`` khi thực thể
        không tồn tại. Chuỗi rỗng là giá trị hợp lệ (xoá ghi chú) — không xoá
        dòng để giữ dấu vết ai xoá.
        """
        text = (note or "").strip()
        if target_type in TABLE_TARGETS:
            StockFlowNote.objects.update_or_create(
                target_type=target_type,
                target_id=target_id,
                defaults={"note": text, "updated_by_id": user_id},
            )
        elif target_type == "stock_document":
            updated = StockDocument.objects.filter(id=target_id).update(note=text)
            if not updated:
                raise LookupError(_NOT_FOUND)
        elif target_type == "return_entry":
            from apps.inventory.application.services.return_entry_service import (
                ReturnEntryService,
            )

            try:
                ReturnEntryService.update_note(entry_id=target_id, note=text)
            except ReturnEntry.DoesNotExist as exc:
                raise LookupError(_NOT_FOUND) from exc
        elif target_type == "pending_scan":
            from apps.inventory.application.services.pending_scan_service import (
                PendingScanService,
            )

            scan = PendingScanService.update_note(scan_id=target_id, note=text)
            if scan is None:
                raise LookupError(_NOT_FOUND)
        else:
            raise ValueError(_UNKNOWN_TARGET)
        return {"target_type": target_type, "target_id": target_id, "note": text}
