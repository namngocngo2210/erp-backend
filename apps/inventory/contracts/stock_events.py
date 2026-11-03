from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class StockWentNegative(DomainEvent):
    event_type: ClassVar[str] = "inventory.stock_negative"
    warehouse_id: int = 0
    product_id: int = 0
    balance_after: int = 0
