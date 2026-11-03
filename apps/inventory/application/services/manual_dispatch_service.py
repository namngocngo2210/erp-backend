"""Xuất kho tay cho một hoặc nhiều đơn, theo kho do người dùng chỉ định.

Nuôi nút "Xuất kho" trên trang chi tiết đơn. Trước 29/08/2026 module này là hàng đợi
"Chờ xuất kho" (`DirectDispatchService`); hàng đợi đã bị gỡ nhưng khối xử lý verdict
KiotViet bên dưới được giữ NGUYÊN VẸN — nó là lời giải cho đúng bài toán mà tính năng
mới cũng gặp, và đã trả giá một lần ở sự cố 2026-07-31.

Khoá chống trừ tồn hai lần nằm sẵn ở `StockSalesService.issue_for_order`
(`@transaction.atomic`, tự gọi `lock_order_stock` rồi guard theo outstanding). KHÔNG tự
mở thêm transaction/lock ở tầng này — xác minh KiotViet (HTTP) chạy qua
`OrderService._prefetch_dispatch_verdicts` NGOÀI transaction, TRƯỚC khi đưa verdict vào
`update_order_inline`; bọc thêm lock ở đây chỉ tạo một khoá no-op nhả ngay cuối câu
lệnh, đọc như có bảo vệ mà thực ra không có.

Riêng khối ghi đè `fulfillment_warehouse_id` + gọi `update_order_inline` của MỘT đơn
được bọc trong một `transaction.atomic()` hẹp — đây KHÔNG phải là lock hay HTTP trong
transaction (hai thứ đoạn trên vừa cấm): không có round-trip KiotViet nào chạy trong
khối này (verdict đã lấy xong ở prefetch, NGOÀI transaction), nó chỉ đảm bảo hai câu
UPDATE của cùng một đơn cùng thành công hoặc cùng bị cuốn lại — nếu `update_order_inline`
raise, việc ghi đè kho phải được huỷ theo, chứ không được để đơn nằm lại với
`fulfillment_warehouse_id` mới nhưng chưa hề bị trừ tồn ở đó. `try/except` gói ngoài
khối atomic (không lồng vào trong): bắt exception NGAY TRONG atomic chỉ đánh dấu
transaction phải rollback rồi để vòng lặp coi như đã xử lý xong — save() bên trong vẫn
"chạy" theo luồng Python dù DB sẽ rollback nó lúc thoát `with`.
"""
from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

# Lý do bỏ qua khi xuất kho — trả thẳng cho FE thay vì im lặng bỏ dòng.
SKIP_NOT_FOUND = "not_found"
SKIP_CANCELLED = "cancelled"
SKIP_ALREADY_DISPATCHED = "already_dispatched"


def _dispatched_order_ids(order_ids: list[int]) -> set[int]:
    """Đơn đã rời kho — hai tín hiệu, đúng một cái là đủ.

    Cùng luật với `flow_order_lookup` (`flow_pending_views.py:85-97`) và
    `_is_dispatched` ở luồng quét. Đơn không khớp sản phẩm nào sẽ không sinh bút
    toán, nên chỉ đọc sổ tồn sẽ báo "chưa xuất" vĩnh viễn.
    """
    from apps.inventory.application.services.stock_sales_service import (
        order_ids_with_outstanding_stock,
    )
    from apps.orders.crm_catalog import DISPATCHED_DELIVERY_CODE
    from apps.orders.models import OrderAssessment

    result = set(order_ids_with_outstanding_stock(order_ids))
    result |= set(
        OrderAssessment.objects.filter(
            order_id__in=order_ids,
            delivery_business_status_code=DISPATCHED_DELIVERY_CODE,
        ).values_list("order_id", flat=True)
    )
    return result


class ManualDispatchService:
    @staticmethod
    def dispatch(*, order_ids: list[int], warehouse_id: int, actor) -> dict:
        """Xuất kho tay: ghi kho đã chọn vào đơn rồi trừ tồn với mốc là thời điểm bấm.

        MỖI ĐƠN MỘT LẦN GỌI ĐỘC LẬP: một đơn hỏng (hoá đơn KiotViet đã bị huỷ trong
        đêm) không được kéo cả lô về rollback — trả `skipped` thay vì im lặng bỏ
        dòng hay raise làm hỏng cả request.

        Xác minh KiotViet chạy SONG SONG cho cả lô (`OrderService._prefetch_dispatch_verdicts`,
        giới hạn 6 worker — cùng lời giải mà `dispatch_orders_batch` đã dùng cho đúng bài
        toán này), NGAY TRƯỚC vòng ghi sổ — không tái dùng verdict lấy từ lúc khác, vì lý do
        "không truyền verdict" ban đầu (bằng chứng cũ cả ngày, hoá đơn có thể đã bị huỷ
        trong đêm) chỉ được giữ nguyên khi prefetch diễn ra sát vòng lặp. Gọi tuần tự từng
        đơn (`update_order_inline_verified` cũ) khiến lô 80 đơn mất tới 240s, sát trần
        `proxy_read_timeout 300s` (nginx/conf.d/default.conf).

        Verdict lỗi (KiotViet timeout/rate-limit lúc prefetch) làm đơn đó SKIP NGAY, KHÔNG
        truyền xuống `update_order_inline`: hàm đó chạy trong `@transaction.atomic` (đã
        `order.save()` + `_upsert_order_assessment()` từ trước khi guard chạy, tức đang giữ
        row lock) — nếu để verdict-là-exception lọt xuống, guard đọc "verdict lạ" nên tự đi
        hỏi lại KiotViet, tái tạo đúng round-trip HTTP TRONG transaction mà tham số
        `verdict` sinh ra để tránh (đọc docstring `update_order_inline`). Lô cả trăm đơn
        lúc KiotViet degrade sẽ thành N round-trip tuần tự, mỗi cái giữ transaction+lock —
        đúng dạng sự cố 2026-07-31. Tự skip ở tầng này giữ đúng ngữ nghĩa "một lượt verify,
        lỗi thì skip" của đường tuần tự cũ, và không tốn round-trip thứ hai. Cố ý KHÔNG
        import `CancellationVerificationUnavailable`/`DispatchVerificationUnavailable` ở
        đây — inventory và orders là hai app cùng tầng (`.importlinter`), thêm import mới
        sẽ đẻ nợ phân tầng; `isinstance(verdict, Exception)` là đủ để nhận diện, không cần
        biết kiểu cụ thể.
        """
        from apps.orders.models import Order, OrderStatus
        from apps.orders.application.services.order_service_impl import OrderService

        confirmed: list[int] = []
        skipped: list[dict] = []

        # Khử trùng lặp, GIỮ THỨ TỰ: id trùng trong order_ids trước đây có thể báo
        # `confirmed` hai lần cho cùng một đơn (dispatched-check tính MỘT LẦN ở vòng dưới
        # nên lần gặp thứ hai không còn bị chặn bởi already_dispatched nữa — không trừ tồn
        # hai lần vì update_order_inline idempotent, nhưng con số trả về sai).
        order_ids = list(dict.fromkeys(order_ids))

        # `Order.objects` không tự lọc deleted_at — đơn xoá mềm không được coi là ứng
        # viên hợp lệ nên rơi vào nhánh not_found. select_related kiotviet_channel +
        # assessment: verifier đọc order.kiotviet_channel (cancellation_verifier.py)
        # trong thread pool của prefetch — thiếu là mỗi worker thread tự mở connection
        # lazy-load FK, tối đa 6 connection thêm mỗi lần bấm (cùng lý do
        # dispatch_orders_batch select_related sẵn 4 quan hệ này).
        orders_by_id: dict[int, Order] = {
            order.id: order
            for order in Order.objects.filter(
                pk__in=order_ids, deleted_at__isnull=True
            ).select_related("customer", "kiotviet_channel", "assessment")
        }

        # Vòng 1: lọc theo trạng thái DB (rẻ, không HTTP) — chỉ đơn còn hợp lệ mới
        # đáng để hỏi KiotViet ở vòng 2.
        candidates: list[Order] = []
        for order_id in order_ids:
            order = orders_by_id.get(order_id)
            if order is None:
                skipped.append({"order_id": order_id, "reason": SKIP_NOT_FOUND})
                continue
            if order.status == OrderStatus.CANCELLED:
                skipped.append({"order_id": order_id, "reason": SKIP_CANCELLED})
                continue
            candidates.append(order)

        dispatched = _dispatched_order_ids([o.id for o in candidates]) if candidates else set()
        to_verify: list[Order] = []
        for order in candidates:
            if order.id in dispatched:
                skipped.append({"order_id": order.id, "reason": SKIP_ALREADY_DISPATCHED})
                continue
            to_verify.append(order)

        # Bằng chứng tươi: prefetch chạy NGAY TRƯỚC vòng ghi sổ bên dưới, không ở chỗ nào
        # khác trong hàm này.
        verdict_map = OrderService._prefetch_dispatch_verdicts(to_verify)

        for order in to_verify:
            verdict = verdict_map.get(order.id)
            if isinstance(verdict, Exception):
                # Round-trip của riêng đơn này lỗi ở prefetch (NGOÀI transaction) — skip
                # NGAY, không truyền exception xuống update_order_inline: hàm đó đang giữ
                # row lock trong transaction.atomic khi tới guard, verdict lạ sẽ khiến
                # guard tự đi hỏi lại KiotViet lần hai — HTTP TRONG transaction, đúng thứ
                # tham số verdict sinh ra để tránh. Một lượt verify, lỗi thì skip.
                logger.warning(
                    "Xuất kho bỏ qua đơn %s: verify KiotViet lỗi ở prefetch (%s)",
                    order.id,
                    verdict,
                )
                skipped.append({"order_id": order.id, "reason": str(verdict)[:200]})
                continue
            try:
                # Bọc CẢ HAI ghi (ghi đè kho + trừ tồn) trong một atomic hẹp: nếu
                # `update_order_inline` raise, ghi đè kho phải bị cuốn lại theo — đơn
                # không được nằm lại với `fulfillment_warehouse_id` mới mà chưa hề bị
                # trừ tồn ở đó (xem docstring module). `try` nằm NGOÀI `with` — bắt
                # exception bên trong atomic chỉ đánh dấu rollback, không thoát ra
                # ngoài để `except` này thấy.
                with transaction.atomic():
                    if order.fulfillment_warehouse_id != warehouse_id:
                        # Ghi đè có chủ đích: sổ kho và đơn phải nói cùng một chuyện.
                        # Ghi TRƯỚC khi ghi sổ vì `update_order_inline` đọc
                        # `order.fulfillment_warehouse_id` để biết trừ ở kho nào.
                        order.fulfillment_warehouse_id = warehouse_id
                        order.save(update_fields=["fulfillment_warehouse", "updated_at"])
                    # `verdict` là kết quả `_prefetch_dispatch_verdicts` vừa lấy song
                    # song ở trên — một `CancelVerdict` thật (đã loại nhánh exception ở
                    # trên). Truyền thẳng xuống: guard đọc đúng bằng `is`, khỏi
                    # round-trip HTTP thứ hai.
                    #
                    # KHÔNG truyền occurred_at: mặc định của `record_movement` là
                    # now(), đúng bằng thời điểm bấm — chính là mốc ta cần. Truyền
                    # `kiotviet_purchase_date` ở đây sẽ phá toàn bộ mục đích tính năng.
                    OrderService.update_order_inline(
                        order,
                        {"crm": {"delivery_business_status_code": "dispatched"}},
                        actor,
                        verdict=verdict,
                    )
            except Exception as exc:
                logger.exception("Xuất kho thất bại cho đơn %s", order.id)
                skipped.append({"order_id": order.id, "reason": str(exc)[:200]})
                continue
            confirmed.append(order.id)

        return {"confirmed": confirmed, "skipped": skipped}
