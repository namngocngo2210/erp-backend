"""Subscriber của app orders: ghi vết `Order.status` vào journey.

Sinh ra từ điều tra 2026-07-23 (docs/erp-revive-stock-safety-net.md). Journey trước đây chỉ
ghi các mã CRM (`sales_status_changed`, `delivery_business_status_changed`, …) nên khi cần
biết đơn đi qua trạng thái HUỶ lúc nào và đường nào lật nó về, không có gì để tra — dấu vết
duy nhất là dòng log của worker, mất theo container mỗi lần deploy.

Nghe THẲNG `OrderStatusChanged` thay vì sửa 8 điểm publish: một chỗ duy nhất, và mọi publisher
hiện có lẫn về sau đều được phủ. Giới hạn đã biết: đường nào không phát sự kiện thì journey
cũng trống — cron đối soát tồn (`audit_revived_orders_stock`) là lưới hứng cho đúng ca đó.
"""
from __future__ import annotations

import logging

from apps.common.events import event_bus
from apps.orders.contracts.order_events import OrderStatusChanged

logger = logging.getLogger(__name__)

STATUS_CHANGED_EVENT = "order_status_changed"


def _status_label(value: int) -> str:
    """Nhãn tiếng Việt của một mã trạng thái; mã lạ trả nguyên số."""
    from apps.orders.models import OrderStatus

    try:
        return OrderStatus(value).label
    except ValueError:
        return str(value)


def _on_order_status_changed(event: OrderStatusChanged) -> None:
    """Ghi 1 journey event cho mỗi lần đổi `Order.status`.

    Nuốt mọi lỗi: journey là dữ liệu QUAN SÁT. Bus đã cô lập handler với nhau, nhưng để
    exception thoát ra đây thì log sẽ đầy traceback của một thứ không ảnh hưởng nghiệp vụ,
    lấn át đúng những cảnh báo cần thấy.
    """
    from apps.orders.models import Order, OrderJourneyEvent

    if event.old_status == event.new_status:
        # Publisher lỡ phát khi không đổi gì → không ghi rác (journey có retention riêng,
        # rác ở đây làm loãng đúng thứ ta cần tra).
        return
    try:
        order = Order.objects.filter(pk=event.order_id).only("id").first()
        if order is None:
            return
        payload = {
            "from_label": _status_label(event.old_status),
            "to_label": _status_label(event.new_status),
        }
        if event.source:
            payload["source"] = event.source
        OrderJourneyEvent.objects.create(
            order=order,
            event_type=STATUS_CHANGED_EVENT,
            from_code=str(event.old_status),
            to_code=str(event.new_status),
            actor_id=event.actor_id,
            payload_json=payload,
        )
    except Exception:  # noqa: BLE001 — xem docstring
        logger.exception("Ghi journey đổi trạng thái thất bại (order_id=%s)", event.order_id)


def register() -> None:
    """Đăng ký subscriber, idempotent theo trạng thái THẬT của bus.

    Cùng lý do với `apps.inventory.subscribers.register`: cờ module nói dối sau
    ``event_bus.clear()`` trong test, hỏi thẳng bus thì gọi lại bao nhiêu lần cũng đúng.
    """
    if not event_bus.is_subscribed(OrderStatusChanged.event_type, _on_order_status_changed):
        event_bus.subscribe(OrderStatusChanged.event_type, _on_order_status_changed)
