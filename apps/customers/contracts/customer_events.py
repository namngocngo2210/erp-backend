"""Domain event app customers phát cho module khác qua event bus in-process.

`customers` KHÔNG biết ai tiêu thụ — consumer tự subscribe ở `ready()` của app
mình. Xem `apps/common/events.py` và `docs/architecture-standard.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class CustomerSaved(DomainEvent):
    """Phát sau MỖI ``Customer.save()`` (tạo lẫn sửa), sau commit.

    Thay cho ``post_save`` mà app khác từng móc thẳng vào model (audit 2026-09-23
    T3/P2-4). ``bulk_*``/``queryset.update()`` không phát — y như signal cũ.
    """

    event_type: ClassVar[str] = "customers.saved"
    customer_id: int = 0
    created: bool = False
    is_deleted: bool = False
