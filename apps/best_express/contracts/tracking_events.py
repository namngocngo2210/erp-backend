"""Domain event Best Express phát cho module khác qua event bus in-process.

`best_express` KHÔNG biết ai tiêu thụ — consumer tự subscribe ở `ready()` của
app mình. Xem `apps/common/events.py` và `docs/architecture-standard.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class BestTrackingUpdated(DomainEvent):
    """Phát khi lưu được 1 event trạng thái MỚI từ Best (webhook hoặc trace sync)."""

    event_type: ClassVar[str] = "best_express.tracking_updated"
    code_best: str = ""
    merchant_order_code: str = ""
    best_status_code: str = ""
    modified_at_ms: int = 0
