"""Task khôi phục đơn đã hủy.

Restore TẠO ĐƠN MỚI (clone qua `OrderService.duplicate_order`): đơn hủy gốc giữ
nguyên làm lịch sử, đơn mới trỏ về nó qua `Order.restored_from`. Cuối batch phát
một event `restore.completed` cho client hiện toast (không bắn `order.created`
từng đơn để tránh spam thông báo khi khôi phục lô lớn).
"""

import logging

from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from apps.channels_core.groups import CRM_GROUP
from apps.orders.models import Order
from apps.orders.application.services.order_service_impl import OrderService

logger = logging.getLogger(__name__)


def _broadcast_restore_completed(
    *,
    user_id: int | None,
    restored_ids: list[int],
    skipped_ids: list[int],
    already_restored_ids: list[int],
    failed_ids: list[int],
) -> None:
    """Phát 1 event riêng cho FE biết toàn bộ batch đã chạy xong → hiện toast.

    `already_restored_ids` tách riêng khỏi `skipped_ids`: bấm khôi phục lần hai
    trên đơn đã có bản khôi phục là thao tác thường gặp (đơn hủy vẫn nằm tab
    "Đã hủy" và vẫn chọn được), không phải "đơn không hợp lệ" như các lý do
    skip còn lại (không tồn tại/chưa hủy).

    `failed_ids` tách riêng nốt: exception khi clone là LỖI HỆ THỐNG, gộp vào
    "không hợp lệ" khiến người vận hành đi soi dữ liệu thay vì soi log worker
    (sự cố 2026-08-03: worker chạy image cũ, NotNullViolation bị toast thành
    "2 đơn không hợp lệ").

    Bypass OrderEventContract vì đây không phải order-level event."""
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    try:
        # Event batch KHÔNG gắn kênh — đi group chung, mọi consumer đều ở đó
        # (route theo kênh chỉ áp cho crm.event có channel_code).
        async_to_sync(channel_layer.group_send)(
            CRM_GROUP,
            {
                "type": "restore.completed",
                "payload": {
                    "user_id": user_id,
                    "restored_count": len(restored_ids),
                    "restored_ids": restored_ids,
                    "skipped_count": len(skipped_ids),
                    "skipped_ids": skipped_ids,
                    "already_restored_count": len(already_restored_ids),
                    "already_restored_ids": already_restored_ids,
                    "failed_count": len(failed_ids),
                    "failed_ids": failed_ids,
                },
            },
        )
    except Exception:
        logger.exception("Failed to broadcast restore.completed batch event")


@shared_task(
    bind=True,
    ignore_result=True,
    name="apps.orders.tasks.process_restore_orders",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
    # Batch không giới hạn số đơn — dính limit toàn cục 25s/30s là bị SIGKILL giữa
    # vòng lặp: nửa batch khôi phục, nửa im lặng bỏ, event restore.completed không
    # bao giờ phát (audit 2026-07-14 vòng 5).
    soft_time_limit=300,
    time_limit=360,
)
def process_restore_orders(self, order_ids: list[int], user_id: int | None = None, restore_show_orders: bool = False):
    """Khôi phục danh sách đơn đã hủy → tạo đơn mới (clone), không sửa đơn cũ.

    - Xử lý đơn "đã hủy" theo đúng quy tắc CANCELLED_ORDER_Q của tab "Đã hủy"
      (invoice_status=2 — hủy hóa đơn KiotViet, HOẶC status=6 — hủy POS/CRM)
      và chưa có bản khôi phục còn sống.
    - Đơn mới: status=NEW, sạch order_code/invoice_*/old_*/shipping_code để KiotViet
      push lại từ đầu, kế thừa created_at đơn gốc, show_orders theo cờ
      `restore_show_orders`, assessment preset sales_status_code='closed'.
    - Đơn cũ: KHÔNG đổi field nào, vẫn nằm tab "Đã hủy".
    - Broadcast duy nhất 1 event restore.completed cuối batch.
    """
    task_id = user_id
    logger.info(
        "Celery task started: process_restore_orders (task_id=%s, user_id=%s, count=%s)",
        task_id, user_id, len(order_ids or []),
    )

    actor = None
    if user_id:
        try:
            from apps.users.models import User

            actor = User.objects.filter(pk=user_id).first()
        except Exception:
            logger.exception("Failed to resolve actor user_id=%s", user_id)

    restored_ids: list[int] = []
    skipped_ids: list[int] = []
    already_restored_ids: list[int] = []
    failed_ids: list[int] = []

    for order_id in (order_ids or []):
        try:
            with transaction.atomic():
                order = (
                    Order.objects.select_for_update()
                    .select_related("channel")
                    .filter(pk=order_id, deleted_at__isnull=True)
                    .first()
                )
                if not order:
                    skipped_ids.append(order_id)
                    continue
                if order.invoice_status != 2 and order.status != 6:
                    # Chỉ khôi phục đơn thực sự đã hủy — cùng định nghĩa với
                    # CANCELLED_ORDER_Q (tab "Đã hủy"): đơn hủy POS chỉ có
                    # status=6, invoice_status vẫn null/1.
                    skipped_ids.append(order_id)
                    continue
                if order.restored_orders.filter(deleted_at__isnull=True).exists():
                    # Đã khôi phục rồi — không tạo bản thứ hai. Lock ở trên khiến
                    # hai request đồng thời không thể cùng lọt qua chỗ này. Tách
                    # riêng khỏi skipped_ids: đây KHÔNG phải đơn không hợp lệ.
                    already_restored_ids.append(order_id)
                    continue

                new_order = OrderService.duplicate_order(
                    order,
                    actor,
                    reset_progress=True,
                    inherit_created_at=True,
                    default_sales_status="closed",
                    show_orders=bool(restore_show_orders),
                    emit_event=False,
                    restored_from=order,
                )

            restored_ids.append(new_order.id)
        except Exception:
            logger.exception("Failed to restore order_id=%s", order_id)
            failed_ids.append(order_id)

    _broadcast_restore_completed(
        user_id=user_id,
        restored_ids=restored_ids,
        skipped_ids=skipped_ids,
        already_restored_ids=already_restored_ids,
        failed_ids=failed_ids,
    )

    logger.info(
        "Celery task finished: process_restore_orders (task_id=%s, restored=%s, "
        "skipped=%s, already_restored=%s, failed=%s)",
        task_id, len(restored_ids), len(skipped_ids), len(already_restored_ids),
        len(failed_ids),
    )
    return {
        "restored_count": len(restored_ids),
        "skipped_count": len(skipped_ids),
        "restored_ids": restored_ids,
        "skipped_ids": skipped_ids,
        "already_restored_count": len(already_restored_ids),
        "already_restored_ids": already_restored_ids,
        "failed_count": len(failed_ids),
        "failed_ids": failed_ids,
    }
