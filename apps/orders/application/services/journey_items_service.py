"""Journey cho vòng đời dòng hàng: "Tạo đơn" và "Đổi sản phẩm / số lượng".

Trước 2026-09-25 cả hai đều không để lại vết: `_replace_order_items` và đồng bộ
KiotViet xoá rồi ghi lại toàn bộ `OrderItem`, nên câu hỏi "ai tạo đơn", "ai đổi số
lượng 1 → 2" không có câu trả lời. Module này chụp dòng hàng TRƯỚC khi ghi, so với
SAU khi ghi và chỉ ghi event khi thật sự đổi.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from apps.orders.models import Order, OrderItem, OrderJourneyEvent
from apps.orders.sheet_rules import ORDER_CREATED_EVENT, ORDER_ITEMS_CHANGED_EVENT

# sku → (tên sản phẩm, tổng số lượng). Gộp theo SKU vì một đơn có thể có nhiều dòng
# cùng SKU (KiotViet tách dòng khuyến mãi) — so theo dòng sẽ báo đổi giả.
ItemsSnapshot = dict[str, tuple[str, int]]

_NAME_MAX = 40


def snapshot_items(order_id: int) -> ItemsSnapshot:
    """Dòng hàng hiện tại của đơn, một query."""
    snapshot: ItemsSnapshot = {}
    rows = OrderItem.objects.filter(order_id=order_id).values_list("sku", "product_name", "quantity")
    for sku, name, quantity in rows:
        key = sku or name or ""
        previous_qty = snapshot[key][1] if key in snapshot else 0
        snapshot[key] = (name or "", previous_qty + int(quantity or 0))
    return snapshot


def _item_label(sku: str, name: str) -> str:
    """Tên sản phẩm ngắn gọn kèm SKU — tên catalog có thể dài cả trăm ký tự."""
    short = name if len(name) <= _NAME_MAX else f"{name[:_NAME_MAX].rstrip()}…"
    if not short:
        return sku
    return f"{short} ({sku})" if sku and sku != name else short


def describe_items_diff(before: ItemsSnapshot, after: ItemsSnapshot) -> list[str]:
    """Các thay đổi dạng người đọc được; rỗng nếu không đổi gì."""
    changes: list[str] = []
    for sku in sorted(before.keys() | after.keys()):
        old = before.get(sku)
        new = after.get(sku)
        if old is not None and new is not None and old[1] == new[1]:
            continue
        label = _item_label(sku, (new or old)[0])  # type: ignore[index]
        if old is None:
            changes.append(f"Thêm {label} x{new[1]}")  # type: ignore[index]
        elif new is None:
            changes.append(f"Bỏ {label} x{old[1]}")
        else:
            changes.append(f"{label}: {old[1]} → {new[1]}")
    return changes


def log_items_changed(
    order: Order,
    before: ItemsSnapshot,
    after: ItemsSnapshot,
    *,
    actor_id: int | None = None,
    source: str = "",
) -> OrderJourneyEvent | None:
    changes = describe_items_diff(before, after)
    if not changes:
        return None
    return OrderJourneyEvent.objects.create(
        order=order,
        event_type=ORDER_ITEMS_CHANGED_EVENT,
        actor_id=actor_id,
        note="; ".join(changes),
        payload_json={
            "source": source,
            "before": {sku: qty for sku, (_, qty) in before.items()},
            "after": {sku: qty for sku, (_, qty) in after.items()},
        },
    )


@contextmanager
def track_items_change(order: Order, actor=None, source: str = "") -> Iterator[None]:
    """Bọc khối ghi lại dòng hàng: chụp trước/sau và ghi journey nếu thật sự đổi."""
    before = snapshot_items(order.id)
    yield
    after = snapshot_items(order.id)
    log_items_changed(order, before, after, actor_id=getattr(actor, "id", None), source=source)


def log_order_created(order: Order, *, actor_id: int | None = None, source: str = "") -> OrderJourneyEvent:
    """Ghi "Tạo đơn" kèm dòng hàng lúc tạo — mốc để so các lần đổi số lượng sau."""
    items = snapshot_items(order.id)
    summary = ", ".join(f"{_item_label(sku, name)} x{qty}" for sku, (name, qty) in items.items())
    return OrderJourneyEvent.objects.create(
        order=order,
        event_type=ORDER_CREATED_EVENT,
        actor_id=actor_id,
        note=summary,
        payload_json={
            "source": source,
            "order_source": order.order_source or "",
            "items": {sku: qty for sku, (_, qty) in items.items()},
        },
    )
