import logging

from celery import shared_task
from django.utils import timezone

from apps.orders.infrastructure.lark_notifier import (
    send_cancelled_dispatch_alert,
    send_order_created_notification,
    send_vat_invoice_notification,
)
from apps.orders.models import Order, OrderItem, OrderVat

logger = logging.getLogger(__name__)

_VAT_LABELS = {
    OrderVat.INDIVIDUAL: "Cá nhân",
    OrderVat.COMPANY: "Công ty",
}


@shared_task(
    bind=True,
    ignore_result=True,
    soft_time_limit=15,
    time_limit=30,
    name="apps.orders.tasks.notify_lark_order_created",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
)
def notify_lark_order_created(self, channel_name: str | None = None, staff_name: str | None = None):
    """Send a best-effort text message to the Lark group when a new order lands."""
    if send_order_created_notification(channel_name=channel_name, staff_name=staff_name):
        logger.info(
            "notify_lark_order_created sent (channel=%s staff=%s)", channel_name, staff_name
        )


def _build_vat_invoice_message(order: Order) -> str:
    """Soạn nội dung tin Lark cho đơn cần xuất VAT: loại VAT, mã đơn/HĐ,
    khách (tên + SĐT). Không kèm mã vận đơn vì thông báo gửi ngay khi đơn
    được đặt cờ VAT (có thể chưa giao kship)."""
    vat_label = _VAT_LABELS.get(order.vat, order.vat)
    order_ref = (order.invoice_code or order.order_code or "").strip() or f"#{order.id}"

    customer = (order.name or "").strip() or "—"
    phone = (order.phone or order.customer_phone or "").strip() or "—"

    return (
        f"🧾 Đơn cần xuất hoá đơn VAT ({vat_label})\n"
        f"• Mã đơn/HĐ: {order_ref}\n"
        f"• Khách: {customer} - {phone}"
    )


@shared_task(
    bind=True,
    ignore_result=True,
    soft_time_limit=15,
    time_limit=30,
    name="apps.orders.tasks.notify_lark_vat_invoice_required",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
)
def notify_lark_vat_invoice_required(self, order_id: int):
    """Báo team kế toán (Lark) khi đơn được đặt cờ cần xuất VAT (cá nhân/công ty).

    Best-effort + idempotent: chỉ gửi 1 lần/đơn (đánh dấu ``vat_invoice_notified_at``).
    """
    order = Order.objects.filter(id=order_id).first()
    if order is None:
        logger.warning("notify_lark_vat_invoice_required: order %s not found", order_id)
        return

    # Phòng vệ: chỉ gửi cho đơn cần xuất VAT (cá nhân/công ty) và chưa gửi.
    if order.vat not in (OrderVat.INDIVIDUAL, OrderVat.COMPANY):
        return
    if order.vat_invoice_notified_at is not None:
        return

    if not send_vat_invoice_notification(_build_vat_invoice_message(order)):
        # Không đánh dấu khi gửi lỗi → lần đặt cờ VAT sau (nếu có) còn cơ hội gửi lại.
        return

    # Đánh dấu đã gửi một cách atomic, chỉ khi vẫn chưa được đánh dấu (chống đua).
    Order.objects.filter(id=order_id, vat_invoice_notified_at__isnull=True).update(
        vat_invoice_notified_at=timezone.now()
    )
    logger.info("notify_lark_vat_invoice_required sent (order_id=%s)", order_id)


# Trần số dòng hàng in ra tin Lark. Đơn thường 1-3 SKU; đơn sỉ dài bất thường sẽ nuốt
# cả màn hình chat và đẩy 3 dòng định danh lên khuất tầm mắt.
_CANCEL_ALERT_ITEM_LIMIT = 10


def _format_cancelled_dispatch_items(items: list[tuple[str, int]]) -> str:
    """Gộp dòng hàng thành chuỗi `SKU xSL`, cắt bớt phần đuôi khi đơn quá dài."""
    if not items:
        return "—"
    shown = items[:_CANCEL_ALERT_ITEM_LIMIT]
    text = ", ".join(f"{sku} x{quantity}" for sku, quantity in shown)
    remaining = len(items) - len(shown)
    if remaining > 0:
        text += f" … và {remaining} SKU nữa"
    return text


def _build_cancelled_dispatch_message(
    *, order_ref: str, shipping_code: str, warehouse_name: str, items: list[tuple[str, int]]
) -> str:
    """Tin báo kho: đơn đã xuất kho vừa bị huỷ, hàng còn ngoài kho.

    Mã đơn, mã vận đơn, kho giao (chốt 2026-08-24) và danh sách SKU theo DÒNG HÀNG CỦA
    ĐƠN (chốt 2026-08-26). Không kèm thông tin khách — tin bắn vào nhóm chat chung.

    Dòng hàng của đơn KHÁC "SL đang nợ" mà tab Chờ hàng hoàn về hiển thị (tab đọc từ
    sổ): đơn đã hoàn một phần vẫn liệt kê đủ SL gốc, và đơn combo hiện mã combo chứ
    không phải các mã lẻ đã nở lúc ghi sổ. Chủ đích: tin cảnh báo khớp với thứ Sale/kho
    nhìn thấy trên đơn, còn số nợ chính xác thì tra ở tab.
    """
    return (
        "🚨 Đơn đã xuất kho bị HUỶ — hàng còn ngoài kho\n"
        f"• Mã đơn: {order_ref}\n"
        f"• Mã vận đơn: {shipping_code or '—'}\n"
        f"• Kho giao: {warehouse_name or '—'}\n"
        f"• Hàng: {_format_cancelled_dispatch_items(items)}"
    )


def _cancelled_dispatch_items(order_id: int) -> list[tuple[str, int]]:
    """Dòng hàng của đơn, theo thứ tự nhập. Một truy vấn, không N+1.

    Lấy hết chứ không `LIMIT` ở DB: cần TỔNG số dòng để in "… và N SKU nữa", và một
    đơn chỉ có vài dòng nên không có gì để tiết kiệm.
    """
    return [
        ((sku or "").strip() or (product_name or "").strip() or "—", quantity)
        for sku, product_name, quantity in OrderItem.objects.filter(order_id=order_id)
        .order_by("id")
        .values_list("sku", "product_name", "quantity")
    ]


@shared_task(
    bind=True,
    ignore_result=True,
    soft_time_limit=15,
    time_limit=30,
    name="apps.orders.tasks.notify_lark_cancelled_after_dispatch",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
)
def notify_lark_cancelled_after_dispatch(self, order_id: int):
    """Báo kho (Lark) khi đơn đã quét xuất kho bị huỷ — hàng phải đòi về.

    Điều kiện bắn nằm ở subscriber kho (`apps.inventory.subscribers`): chỉ khi dòng
    theo dõi 'chờ hàng về' được MỞ MỚI, tức sổ kho xác nhận hàng đang thật sự ngoài
    kho. Task này chỉ soạn tin và gửi, best-effort.

    MỘT cửa chặn cuối ngay trước khi gửi: đơn có "hoá đơn tương tự" (`<gốc>.NN`,
    CÙNG mã vận đơn) đang sống thì đây KHÔNG phải huỷ-đòi-hàng-về — KiotViet chỉ
    đổi đơn sang hoá đơn khác (ca giảm giá COD), hàng vẫn đang giao bình thường
    dưới mã vận đơn cũ. Dùng đúng `find_live_reissued_sibling` mà nhánh chuyển sổ
    dùng, không tự chế luật thứ hai (xem docstring module `reissue_sibling`).

    Vì sao check ở ĐÂY chứ không ở subscriber: hoá đơn `.NN` không phải lúc nào cũng
    về DB trước webhook huỷ (ca về sau qua cron import). Subscriber đã hoãn task
    `_CANCEL_DISPATCH_ALERT_DELAY_SECONDS` để tới lúc gửi thì bản `.NN` kịp về —
    check sớm hơn là check trên dữ liệu chưa đủ.
    """
    # Import cục bộ: orders → inventory là import vượt tầng ngang (xem .importlinter),
    # giữ ở phạm vi hàm để không kéo cả cây inventory vào lúc load task module.
    from apps.inventory.application.services.reissue_sibling import (
        find_live_reissued_sibling,
    )

    sibling_id = find_live_reissued_sibling(order_id)
    if sibling_id is not None:
        logger.info(
            "notify_lark_cancelled_after_dispatch skipped (order_id=%s): có đơn xuất "
            "lại %s cùng mã vận đơn — chỉ đổi hoá đơn, không phải huỷ sau xuất kho",
            order_id, sibling_id,
        )
        return

    # `all_objects`: đọc một dòng theo PK, không phải liệt kê đơn nội bộ — lọc phạm vi
    # ở đây chỉ khiến đơn mirror Foxia im lặng không có cảnh báo (cùng lý do với
    # `apps.inventory.subscribers._is_direct_sale`).
    row = (
        Order.all_objects.filter(id=order_id)
        .values("order_code", "invoice_code", "shipping_code", "fulfillment_warehouse__name")
        .first()
    )
    if row is None:
        logger.warning("notify_lark_cancelled_after_dispatch: order %s not found", order_id)
        return

    order_ref = (row["invoice_code"] or row["order_code"] or "").strip() or f"#{order_id}"
    send_cancelled_dispatch_alert(
        _build_cancelled_dispatch_message(
            order_ref=order_ref,
            shipping_code=(row["shipping_code"] or "").strip(),
            warehouse_name=(row["fulfillment_warehouse__name"] or "").strip(),
            items=_cancelled_dispatch_items(order_id),
        )
    )
    logger.info("notify_lark_cancelled_after_dispatch sent (order_id=%s)", order_id)
