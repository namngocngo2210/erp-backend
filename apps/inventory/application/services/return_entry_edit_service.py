"""Sửa metadata nhóm đơn hoàn. Không đụng tồn kho: mọi thay đổi tồn nằm ở
ReturnEntryService.post_entry / cancel_entry.

`update_draft` (khớp đơn thủ công vào nhóm nháp) đã gỡ 2026-07-14 cùng UI phiếu
trả — khớp tay giờ đi qua `PendingScanService.manual_match` (bảng chờ mã quét).
"""
from __future__ import annotations

from django.db import transaction

from apps.inventory.models import ReturnEntry


@transaction.atomic
def update_note(*, entry_id: int, note: str) -> ReturnEntry:
    """Sửa ghi chú nhóm đơn ở mọi trạng thái (kể cả đã ghi sổ).

    Ghi chú là metadata, không đụng tồn kho hay trạng thái nhóm đơn/đơn nguồn
    nên không giới hạn theo status.
    """
    entry = ReturnEntry.objects.select_for_update().get(id=entry_id)
    entry.note = note or ""
    entry.save(update_fields=["note"])
    return entry
