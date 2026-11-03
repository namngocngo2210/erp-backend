from __future__ import annotations

import logging

from django.db import transaction

from apps.common.events import event_bus
from apps.inventory.application.services.reissue_sibling import find_live_reissued_sibling
from apps.inventory.application.services.stock_sales_service import CANCEL_RELEASE_NOTE
from apps.inventory.contracts.stock_events import StockWentNegative
from apps.inventory.contracts.stock_sales import StockSalesService
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.infrastructure.lark_notifier import send_stock_negative_alert
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

# OrderStatus HUỶ = 6. Từ 2026-07-29 huỷ đơn KHÔNG còn tự hoàn tồn; tập này giờ chỉ
# dùng để nhận ra "đơn rời/quay lại trạng thái huỷ".
_RELEASE_STATUSES = frozenset({6})
# OrderStatus RETURNED = 5. Trước 2026-09-14 ghi sổ phiếu hoàn đặt đơn huỷ sang trạng
# thái này (chuyển 6 → 5 nhưng KHÔNG phải hồi sinh: hàng vừa về kho, không có gì để
# trừ lại). Từ 2026-09-14 đơn huỷ giữ nguyên 6 khi hàng về; nhánh loại trừ vẫn giữ làm
# phòng thủ cho mọi đường 6 → 5 khác (spec 2026-07-29 §4, ghi chú cập nhật).
_RETURNED_STATUS = 5
# Hoãn tin "huỷ sau xuất kho" 10 phút trước khi gửi. Hoá đơn xuất lại `<gốc>.NN`
# (ca KiotViet đổi hoá đơn để giảm giá COD) không phải lúc nào cũng về DB trước
# webhook huỷ — có ca về sau qua cron import. Check ngay tại đây sẽ chạy trên dữ
# liệu chưa đủ và vẫn báo nhầm, nên cửa chặn thật nằm ở
# `notify_lark_cancelled_after_dispatch`, ngay trước lúc gửi. 10 phút là đủ cho
# một vòng import mà kho vẫn kịp đòi hàng về.
_CANCEL_DISPATCH_ALERT_DELAY_SECONDS = 600


def _describe_stock_negative(event: StockWentNegative) -> str:
    """Build a human-readable alert line, resolving warehouse/product labels.

    Chỉ 2 truy vấn nhẹ theo PK cho 1 sự kiện — không có nguy cơ N+1.
    """
    from apps.catalog.models import Product
    from apps.warehouses.models import Warehouse

    warehouse = (
        Warehouse.objects.filter(pk=event.warehouse_id)
        .values_list("name", flat=True)
        .first()
    )
    product = (
        Product.objects.filter(pk=event.product_id)
        .values_list("sku", flat=True)
        .first()
    )
    warehouse_label = warehouse or f"#{event.warehouse_id}"
    product_label = product or f"#{event.product_id}"
    return (
        f"⚠️ Tồn kho âm: kho [{warehouse_label}] sản phẩm [{product_label}] "
        f"số dư còn {event.balance_after}. Vui lòng kiểm tra nhập/xuất kho."
    )


def _on_stock_negative(event: StockWentNegative) -> None:
    logger.warning(
        "Tồn âm: kho=%s sp=%s số dư=%s",
        event.warehouse_id, event.product_id, event.balance_after,
    )
    send_stock_negative_alert(_describe_stock_negative(event))


def _is_direct_sale(order_id: int) -> bool:
    """Đơn bán quầy đã trừ tồn NGAY lúc lên hoá đơn — chỉ nhóm đơn CŨ.

    `direct_dispatch_deferred=False` + `shipping_type='direct'` = đơn tạo TRƯỚC
    28/08/2026, thời kỳ worker còn trừ tồn ngay khi hoá đơn KiotViet tạo xong. Huỷ những
    đơn đó thì hàng còn trong kho nên cộng trả tồn ngay là đúng.

    Đơn từ 28/08/2026 trở đi mang cờ True và CHƯA bị trừ tồn lúc lên hoá đơn, nên rơi
    xuống nhánh "chờ hàng về" — nhánh đó phán quyết bằng SỔ chứ không bằng nhãn nên tự
    đúng ở cả hai trạng thái (chưa xuất → không mở watch; đã xuất → mở watch).

    ĐỪNG bỏ điều kiện `direct_dispatch_deferred=False`. Xem
    docs/superpowers/specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
    """
    from apps.orders.models import Order, ShippingType

    return Order.all_objects.filter(
        pk=order_id,
        shipping_type=ShippingType.DIRECT,
        direct_dispatch_deferred=False,
    ).exists()


def _alert_cancelled_after_dispatch(order_id: int) -> None:
    """Báo kho qua Lark: đơn đã quét xuất kho vừa bị huỷ, hàng phải đòi về.

    Gửi qua Celery chứ không gọi HTTP tại chỗ: handler này chạy trong luồng huỷ đơn
    (webhook KiotViet là đường nóng), Lark chậm/lỗi không được kéo theo. Webhook chưa
    cấu hình thì bỏ qua luôn cả việc enqueue — giống 2 bot Lark hiện có.
    """
    if not sysconfig.get("LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL"):
        return
    from apps.dispatch import dispatch

    dispatch(
        "lark.notify_cancelled_after_dispatch",
        queue="lark_noti",
        countdown=_CANCEL_DISPATCH_ALERT_DELAY_SECONDS,
        order_id=order_id,
    )


def _on_order_status_changed(event: OrderStatusChanged) -> None:
    """Huỷ đơn → GIỮ NGUYÊN tồn đã trừ; đơn huỷ được HỒI SINH → trừ lại phần đã hoàn.

    Huỷ đơn KHÔNG còn tự hoàn tồn (từ 2026-07-29): hàng đã rời kho thì phải chờ kho
    quét nhập hoàn mới được cộng lại — bút toán phản ánh hàng vật lý, không phản ánh
    giấy tờ. Xem docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md.

    Nhánh hồi sinh vẫn cần: (1) dữ liệu CŨ đã được auto-hoàn thì phải trừ lại
    (`reissue_after_cancel_release` đọc sổ nên tự no-op với đơn theo luật mới); (2) MỌI
    đơn hồi sinh đều phải resync tồn về đúng items hiện tại — KiotViet huỷ-xuất lại
    chính là cách Sale sửa dòng hàng, đơn quay lại có thể mang SKU khác hẳn.

    Nhưng chuyển 6 → RETURNED thì KHÔNG vào nhánh này: đó là ghi sổ phiếu hoàn (hàng vừa
    về kho), không phải hồi sinh để giao tiếp. Không loại ra thì resync chạy trên đơn vừa
    hoàn và trừ tồn oan (spec §4).
    """
    if event.new_status in _RELEASE_STATUSES:
        # KV huỷ-xuất lại nhưng bản `.NN` đã thành DÒNG ĐƠN MỚI (về trước webhook
        # huỷ, hoặc về qua cron import nên không reattach được): hàng vẫn ngoài
        # kho theo đơn mới → CHUYỂN SỔ xuất sang đơn đó thay vì thả về tồn.
        # Không chuyển thì "trừ lại khi hồi sinh" không bao giờ chạy (đơn hồi
        # sinh là bản ghi khác) → tồn phồng vĩnh viễn (prod 2026-07-18: 9 đơn).
        sibling_id = find_live_reissued_sibling(event.order_id)
        if sibling_id is not None and StockSalesService.transfer_issue_to_sibling(
            order_id=event.order_id, sibling_order_id=sibling_id
        ):
            logger.info(
                "Huỷ đơn %s → chuyển sổ xuất sang đơn xuất lại %s",
                event.order_id, sibling_id,
            )
            return
        # Đơn bán trực tiếp: khách lấy hàng tại quầy, huỷ nghĩa là hàng còn ngay trong
        # kho → cộng trả tồn luôn. Bắt kho quét nhập hoàn cho đơn quầy là tạo ra một
        # hàng đợi không bao giờ có ai xử lý.
        if _is_direct_sale(event.order_id):
            logger.info("Huỷ đơn bán trực tiếp %s → hoàn tồn ngay", event.order_id)
            # PHẢI dùng đúng CANCEL_RELEASE_NOTE — không tự chế chuỗi riêng. 4 nơi lọc
            # theo đúng note này để nhận ra "bút toán hoàn tồn do huỷ":
            # pending_cancel_release_by_key (fixer hồi sinh + reclaim_auto_release),
            # RevivedStockAuditService (lưới hứng chạy đêm) và
            # dispatch_history_service._POST_DISPATCH_REVERSAL_NOTES (Lịch sử nhập
            # xuất). Note lạ thì cả 4 chỗ đều mù, đơn direct hồi sinh sẽ không được trừ
            # lại tồn mà không ai biết cho tới khi kiểm kê (xem
            # docstring `pending_cancel_release_by_key`: cấm "hai công thức song
            # song"). Đánh đổi CHẤP NHẬN: đơn direct huỷ sẽ được
            # dispatch_history_service tính là "đã có một lần rời kho" trong Lịch sử
            # nhập xuất, giống hệt đơn shipment huỷ — với bán quầy đó là cách hiểu
            # đúng (hàng đã thật sự rời kho một lần, dù chỉ ra tới quầy tính tiền).
            try:
                StockSalesService.release_for_order(
                    order_id=event.order_id,
                    note=CANCEL_RELEASE_NOTE,
                )
            except Exception:
                # Cùng khuôn với nhánh hồi sinh ngay dưới: handler chạy trong
                # on_commit, bus nuốt exception → rollback ở đây nghĩa là TỒN KHÔNG
                # ĐƯỢC HOÀN mà không ai biết (chỉ 1 dòng log). Lệch về phía THIẾU tồn
                # (ít nguy hiểm hơn tồn phồng) nhưng im lặng vẫn sai — bắn Lark để còn
                # hoàn tồn bằng tay, rồi re-raise cho bus log.
                send_stock_negative_alert(
                    f"⚠️ Huỷ đơn bán trực tiếp #{event.order_id} KHÔNG hoàn được tồn "
                    "(release_for_order lỗi, đã rollback). Vui lòng kiểm tra log và "
                    "hoàn tồn thủ công."
                )
                raise
            return

        # KHÔNG hoàn tồn ở đây: hàng đã rời kho và đang trên đường về. Tồn chỉ được
        # cộng khi kho quét nhập hoàn (phiếu hoàn ghi sổ). Outstanding giữ nguyên
        # chính là "khoản nợ" để phiếu hoàn cộng vào — và là điều kiện để
        # `_guard_double_credit` cho phiếu đó đi qua.
        logger.info(
            "Huỷ đơn %s → giữ nguyên outstanding, chờ hàng về kho", event.order_id
        )
        # Import cục bộ: awaiting_return_service import ngược lại stock_sales_service.
        from apps.inventory.application.services.awaiting_return_service import (
            AwaitingReturnService,
        )

        if AwaitingReturnService.open_for_order(event.order_id):
            _alert_cancelled_after_dispatch(event.order_id)
    elif (
        event.old_status in _RELEASE_STATUSES
        and event.new_status != _RETURNED_STATUS
    ):
        logger.info(
            "Hồi sinh đơn %s (%s → %s) → trừ lại tồn đã hoàn",
            event.order_id, event.old_status, event.new_status,
        )
        # Một transaction cho cả cặp: nếu resync lỗi mà reissue đã commit thì tồn kẹt
        # nửa chừng (đã trừ SKU cũ, chưa sang SKU mới) — hoặc cùng thành công hoặc
        # cùng rollback (audit 2026-07-14 vòng 4). Advisory lock 61001 là xact-scoped
        # nên gộp chung còn giữ lock liền mạch giữa 2 bước.
        try:
            with transaction.atomic():
                StockSalesService.reissue_after_cancel_release(order_id=event.order_id)
                _resync_to_current_items(event.order_id)
        except Exception:
            # Handler chạy trong on_commit, bus nuốt exception → rollback ở đây nghĩa là
            # TỒN KHÔNG ĐƯỢC TRỪ LẠI mà không ai biết (chỉ 1 dòng log). Bắn Lark để còn
            # chạy lại reissue bằng tay (audit 2026-07-14 vòng 5), rồi re-raise cho bus log.
            send_stock_negative_alert(
                f"⚠️ Hồi sinh đơn #{event.order_id} KHÔNG trừ lại được tồn (reissue/resync "
                "lỗi, đã rollback). Tồn đang phồng — chạy lại "
                f"StockSalesService.reissue_after_cancel_release(order_id={event.order_id}) "
                "sau khi xử lý lỗi trong log."
            )
            raise


def _resync_to_current_items(order_id: int) -> None:
    """Sau khi trừ lại tồn, kéo tồn về ĐÚNG items hiện tại của đơn.

    `reissue_after_cancel_release` đọc sổ nên trừ lại đúng SKU/kho mà lần huỷ đã cộng
    trả — nhưng KiotViet huỷ-xuất lại hoá đơn CHÍNH LÀ cách Sale sửa dòng hàng của hoá
    đơn đã hoàn thành. Đơn hồi sinh có thể mang bộ items khác hẳn: không resync thì SKU
    cũ bị trừ vĩnh viễn dù không còn trong đơn, SKU mới không bao giờ bị trừ, và khi
    hàng hoàn về thì `_guard_double_credit` chặn vì SKU mới chưa từng bị trừ — mã quét
    chết cứng (audit 2026-07-14 vòng 3).

    `resync_for_order` tự no-op khi đơn chưa xuất kho (outstanding = 0), nên ca hồi sinh
    đơn chưa từng rời kho không bị đụng tới.
    """
    from apps.inventory.contracts.stock_sales import SaleLine
    from apps.orders.models import Order

    order = (
        Order.objects.filter(pk=order_id)
        .only("id", "fulfillment_warehouse_id")
        .prefetch_related("items")
        .first()
    )
    if order is None or not order.fulfillment_warehouse_id:
        return
    StockSalesService.resync_for_order(
        order_id=order_id,
        warehouse_id=order.fulfillment_warehouse_id,
        lines=[
            SaleLine(product_id=item.product_id, quantity=item.quantity)
            for item in order.items.all()
            if item.product_id and item.quantity > 0
        ],
    )


def register() -> None:
    """Đăng ký subscriber kho lên bus, idempotent theo TRẠNG THÁI THẬT của bus.

    Không dùng cờ module (`_registered`): cờ đó nói dối khi ai đó gọi
    ``event_bus.clear()`` — bus rỗng nhưng cờ vẫn True nên register() bỏ qua,
    handler hoàn kho khi huỷ đơn biến mất vĩnh viễn trong process đó. Hỏi thẳng
    bus (giống ``apps.users.subscribers.register``) thì gọi lại bao nhiêu lần
    cũng đúng và luôn khôi phục được.
    """
    for event_type, handler in (
        (StockWentNegative.event_type, _on_stock_negative),
        (OrderStatusChanged.event_type, _on_order_status_changed),
    ):
        if not event_bus.is_subscribed(event_type, handler):
            event_bus.subscribe(event_type, handler)
