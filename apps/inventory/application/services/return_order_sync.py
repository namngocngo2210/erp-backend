from __future__ import annotations

import logging

from django.db import transaction

from apps.orders.models import OrderStatus

logger = logging.getLogger(__name__)


def mark_order_returned(order, *, user_id: int | None = None) -> int | None:
    """Đặt đơn nguồn = RETURNED (đã chuyển hoàn) + đẩy KiotViet.

    Trả về trạng thái CŨ (int) để lưu vào ReturnNote.order_status_before phục vụ
    hoàn tác khi huỷ. Trả None nếu không đổi gì: đơn đã RETURNED sẵn, hoặc đơn ĐÃ HUỶ
    (giữ nguyên "Đã hủy" — xem ghi chú trong hàm).
    """
    if int(order.status) == int(OrderStatus.RETURNED):
        return None
    # Đơn ĐÃ HUỶ mà hàng về kho vẫn là "Đã hủy": phiếu hoàn chỉ ghi sổ tồn, không đổi
    # trạng thái đơn (quyết định 14/09/2026, thay mục 4 spec 2026-07-29). Trước đây
    # 6 → 5 rồi cron đồng bộ KiotViet 30' lật lại 6 (hoá đơn đã huỷ, delivery "Đã
    # hủy"): 35 đơn/14 ngày giằng qua lại, và đẩy 5 lên KiotViet luôn bị từ chối.
    # "Hàng đã về chưa" vẫn theo dõi được qua CancelledDispatchWatch, không cần status.
    if int(order.status) == int(OrderStatus.CANCELLED):
        logger.info("Đơn %s đã huỷ: nhập hoàn chỉ ghi sổ tồn, giữ trạng thái Đã hủy.", order.id)
        return None
    previous = int(order.status)
    order.status = int(OrderStatus.RETURNED)
    order.save(update_fields=["status"])
    _publish_status_changed(order, previous, int(OrderStatus.RETURNED), user_id)
    # Đẩy KiotViet SAU khi commit: giải phóng khoá hàng (select_for_update trên
    # đơn + phiếu trong post_return) trước khi gọi HTTP có thể mất tới 30s.
    transaction.on_commit(lambda: _push_kiotviet(order, int(OrderStatus.RETURNED)))
    return previous


def revert_order_status(
    order, previous_status: int, *, user_id: int | None = None
) -> None:
    """Trả đơn về trạng thái trước khi bị đánh dấu RETURNED + đẩy KiotViet."""
    current = int(order.status)
    order.status = int(previous_status)
    order.save(update_fields=["status"])
    _publish_status_changed(order, current, int(previous_status), user_id)
    # Đẩy KiotViet sau commit — xem ghi chú ở mark_order_returned.
    transaction.on_commit(lambda: _push_kiotviet(order, int(previous_status)))


def _publish_status_changed(
    order, old_status: int, new_status: int, user_id: int | None
) -> None:
    if old_status == new_status:
        return
    from apps.common.events import event_bus
    from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource

    event_bus.publish_on_commit(
        OrderStatusChanged(
            order_id=order.id,
            old_status=old_status,
            new_status=new_status,
            actor_id=user_id,
            source=StatusChangeSource.STOCK_RETURN,
        )
    )


def _push_kiotviet(order, status: int) -> None:
    """Đẩy deliveryDetail.status của invoice kênh chính lên KiotViet.

    Lỗi KiotViet CHỈ log — không được làm hỏng ghi sổ/huỷ phiếu.
    """
    if not order.kiotviet_channel_id or not order.invoice_id:
        logger.info(
            "Bỏ qua đẩy KiotViet cho đơn %s: thiếu kiotviet_channel/invoice_id.",
            order.id,
        )
        return
    from apps.inventory.contracts.kiotviet_delivery_port import kiotviet_delivery_port

    try:
        kiotviet_delivery_port().update_invoice_delivery_status(
            invoice_id=order.invoice_id,
            status=status,
            order=order,
            channel=order.kiotviet_channel,
        )
    except Exception as exc:  # noqa: BLE001 — lỗi KiotViet không được chặn nghiệp vụ
        logger.warning(
            "Đẩy trạng thái KiotViet thất bại (order_id=%s, status=%s): %s",
            order.id, status, exc,
        )
