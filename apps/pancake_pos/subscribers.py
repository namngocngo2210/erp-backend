"""Đánh dấu sản phẩm cần đồng bộ tăng dần lên Pancake POS (§3.5 — outbox nhẹ).

Nghe domain event của catalog (không móc `post_save`, không import model catalog):
- `catalog.product_saved`: mọi `Product.save()` — form sản phẩm, xoá mềm, gán nhóm,
  import Excel đơn tạo SKU, import KiotViet (`get_or_create`/`save`).
- `catalog.products_changed`: đường bulk/bảng phụ không qua `save()` (thành phần combo).
- `catalog.product_group_saved`: đổi mã/xoá nhóm → mọi sản phẩm của nhóm.

Handler chạy SAU commit (bus `publish_on_commit`) và chỉ INSERT vào hàng đợi — beat
`sync_dirty_products` mới gọi Pancake. Cờ `PANCAKE_POS_ENABLED` tắt → bỏ qua (trước
khi bật phải chạy dry-run + full theo quy trình, nên không cần tích dirty).
Lỗi nuốt + log: đồng bộ Pancake là phụ, không được phá luồng lưu sản phẩm.

Đẩy đơn (two-way-sync A2) nghe event của orders: `orders.status_changed` (huỷ / đổi
trạng thái) và `orders.saved` có `sales_closed_at` (sale vừa chốt) → xếp task
`push_orders_for` cho đúng đơn đó. Beat 2 phút quét bù mọi ca event không bắt được
(chốt trong nội bộ nhóm, sửa dòng hàng, địa chỉ…). Cờ đẩy đơn tắt → bỏ qua.
"""

from __future__ import annotations

import logging

from apps.catalog.contracts.catalog_events import ProductGroupSaved, ProductSaved, ProductsChanged
from apps.common.events import event_bus
from apps.orders.contracts.order_events import OrderSaved, OrderStatusChanged
from apps.pancake_pos.repositories import DirtyQueue
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(sysconfig.get("PANCAKE_POS_ENABLED"))


def _on_product_saved(event: ProductSaved) -> None:
    if not _enabled() or not event.product_id:
        return
    try:
        DirtyQueue.mark([event.product_id])
    except Exception:  # noqa: BLE001 — không phá luồng lưu sản phẩm
        logger.exception("pancake_pos: không đánh dấu dirty sản phẩm %s", event.product_id)


def _on_products_changed(event: ProductsChanged) -> None:
    if not _enabled() or not event.product_ids:
        return
    try:
        DirtyQueue.mark(list(event.product_ids))
    except Exception:  # noqa: BLE001
        logger.exception("pancake_pos: không đánh dấu dirty %d sản phẩm (%s)", len(event.product_ids), event.reason)


def _on_group_saved(event: ProductGroupSaved) -> None:
    if not _enabled() or event.created or not event.group_id:
        return  # nhóm mới tạo chưa có sản phẩm; sản phẩm gán vào sẽ tự phát product_saved
    try:
        DirtyQueue.mark_group(event.group_id)
    except Exception:  # noqa: BLE001
        logger.exception("pancake_pos: không đánh dấu dirty nhóm %s", event.group_id)


def _push_orders_enabled() -> bool:
    return _enabled() and bool(sysconfig.get("PANCAKE_POS_PUSH_ORDERS_ENABLED"))


def _enqueue_order(order_id: int) -> None:
    from apps.pancake_pos.tasks import push_orders_for

    try:
        push_orders_for.delay([order_id])
    except Exception:  # noqa: BLE001 — broker lỗi: beat 2 phút quét bù
        logger.exception("pancake_pos: không xếp được task đẩy đơn %s", order_id)


def _on_order_status_changed(event: OrderStatusChanged) -> None:
    if event.order_id and _push_orders_enabled():
        _enqueue_order(event.order_id)


def _on_order_saved(event: OrderSaved) -> None:
    fields = event.update_fields or ()
    if event.order_id and "sales_closed_at" in fields and _push_orders_enabled():
        _enqueue_order(event.order_id)


_SUBSCRIPTIONS = (
    (ProductSaved.event_type, _on_product_saved),
    (ProductsChanged.event_type, _on_products_changed),
    (ProductGroupSaved.event_type, _on_group_saved),
    (OrderStatusChanged.event_type, _on_order_status_changed),
    (OrderSaved.event_type, _on_order_saved),
)


def register() -> None:
    """Idempotent theo trạng thái thật của bus (xem apps/cnv_loyalty/subscribers.py)."""
    for event_type, handler in _SUBSCRIPTIONS:
        if not event_bus.is_subscribed(event_type, handler):
            event_bus.subscribe(event_type, handler)
