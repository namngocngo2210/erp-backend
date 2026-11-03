import logging
from datetime import timedelta

from celery import shared_task
from django.db import OperationalError
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.inventory.application.services.partition_service import PartitionService
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.reissue_ledger_repair_service import (
    ReissueLedgerRepairService,
)
from apps.inventory.application.services.revived_stock_audit_service import (
    RevivedStockAuditService,
)
from apps.orders.infrastructure.lark_notifier import send_stock_negative_alert

logger = logging.getLogger(__name__)

# Khóa single-flight: chống 2 lần purge chạy chồng, theo đúng pattern purge
# audit_logs (apps.audit.tasks.purge_old_audit_logs).
_PENDING_SCAN_PURGE_LOCK_KEY = "lock:purge_matched_pending_scans"
_PENDING_SCAN_PURGE_LOCK_TTL = 900  # > time_limit=600
_PENDING_SCAN_PURGE_BATCH_SIZE = 10_000


@shared_task(
    bind=True,
    name="apps.inventory.tasks.ensure_stock_movements_partition",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=60,
    time_limit=90,
)
def ensure_stock_movements_partition(self) -> dict:
    """Đảm bảo partition của tháng hiện tại + tháng kế đã tồn tại (chạy trước khi tháng lăn).

    Mỗi tháng cô lập: lỗi tháng này không chặn tháng kia. Lỗi được log ở mức error
    để ops cảnh báo (kèm khi stock_movements_default phình do lỡ nhịp)."""
    now = timezone.now()
    created = []
    errors = []
    for offset in (0, 1):
        month0 = now.month - 1 + offset
        year = now.year + month0 // 12
        month = month0 % 12 + 1
        try:
            created.append(PartitionService.ensure_month_partition(year, month))
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "failed ensuring stock_movements partition %04d-%02d", year, month
            )
            errors.append({"year": year, "month": month, "error": str(exc)})
    logger.info("ensured stock_movements partitions: %s (errors=%s)", created, errors)
    return {"partitions": created, "errors": errors}


@shared_task(
    bind=True,
    name="apps.inventory.tasks.match_pending_shipment_scans",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=100,
    time_limit=120,
)
def match_pending_shipment_scans(self) -> dict:
    """Cron 60s: khớp mã vận đơn trong bảng chờ với Order đã sync từ KiotViet.

    Single-flight lock (cache.add) chống tick chồng khi lần chạy trước còn dang dở
    — hai run cùng xử lý một scan có thể tạo phiếu trả TRÙNG. Lock tự hết hạn
    nếu worker chết giữa chừng."""
    lock_key = "inventory:match_pending_scans:lock"
    if not cache.add(lock_key, 1, 130):  # ttl > time_limit
        logger.info("match_pending_shipment_scans skip: lock held")
        return {"skipped": "lock_held"}
    try:
        summary = PendingScanService.match_pending_scans()
    finally:
        cache.delete(lock_key)
    logger.info("match_pending_shipment_scans done: %s", summary)
    return summary


@shared_task(
    name="apps.inventory.tasks.sweep_reissue_ledger",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=100,
    time_limit=120,
)
def sweep_reissue_ledger() -> dict:
    """Lưới an toàn cho ca "huỷ đơn → xuất lại" (docs/reissue-ledger-leak-2026-07-21.md).

    `transfer_issue_to_sibling` bắt tại đúng khoảnh khắc webhook huỷ về; hai luồng
    KiotViet độc lập đến sai thứ tự là trượt, và khi trượt thì KHÔNG có đường tự
    khớp lại — tồn phồng vĩnh viễn (prod 2026-07-21: 10 cặp, 12 sản phẩm).

    Ghi bù xong vẫn BẮN CẢNH BÁO: sổ tự lành mà im lặng thì lỗi gốc không ai sửa.
    """
    summary = ReissueLedgerRepairService.sweep()
    if summary["repaired"]:
        pairs = ", ".join(
            f"#{p['cancelled_order_id']}→#{p['sibling_order_id']} ({p['quantity']})"
            for p in summary["pairs"]
        )
        send_stock_negative_alert(
            f"⚠️ Đã ghi bù sổ xuất cho {summary['repaired']} đơn xuất lại bị bỏ rơi "
            f"(tồn từng phồng): {pairs}. Kiểm tra vì sao nhánh chuyển sổ lúc huỷ đơn "
            "không bắt được."
        )
    logger.info("sweep_reissue_ledger done: %s", summary)
    return summary


@shared_task(
    name="apps.inventory.tasks.audit_revived_orders_stock",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=100,
    time_limit=120,
)
def audit_revived_orders_stock() -> dict:
    """Đối soát tồn của đơn huỷ được hồi sinh (docs/erp-revive-stock-safety-net.md).

    Nhánh trừ lại tồn chạy trong `on_commit` handler — lỗi bị bus nuốt, dấu vết chỉ là một
    dòng log mất theo container mỗi lần deploy. Task này đối chiếu thẳng sổ movement nên
    KHÔNG phụ thuộc việc sự kiện có tới được handler hay không: nó bắt được cả những đường
    hụt chưa ai biết (đúng ca 2026-07-23, phải có người đi tìm mới thấy).

    CHỈ cảnh báo, KHÔNG tự ghi sổ — xem docstring `RevivedStockAuditService`. Danh sách
    rỗng thì im lặng, không tạo nhiễu để lúc có cảnh báo thật còn được đọc.
    """
    leaks = RevivedStockAuditService.find_unreissued()
    if leaks:
        detail = ", ".join(
            f"#{leak.order_id} {leak.order_code or leak.shipping_code or '?'} ({leak.quantity})"
            for leak in leaks[:20]
        )
        if len(leaks) > 20:
            detail += f", … (+{len(leaks) - 20} đơn nữa)"
        send_stock_negative_alert(
            f"⚠️ {len(leaks)} đơn huỷ đã hồi sinh nhưng CHƯA trừ lại tồn — tồn đang phồng: "
            f"{detail}. Kiểm tra rồi chạy "
            "StockSalesService.reissue_after_cancel_release(order_id=...) cho từng đơn."
        )
    summary = {
        "found": len(leaks),
        "orders": [
            {"order_id": leak.order_id, "order_code": leak.order_code, "quantity": leak.quantity}
            for leak in leaks
        ],
    }
    logger.info("audit_revived_orders_stock done: %s", summary)
    return summary


@shared_task(
    name="apps.inventory.tasks.purge_matched_pending_scans",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=540, time_limit=600,
)
def purge_matched_pending_scans(batch_size: int = _PENDING_SCAN_PURGE_BATCH_SIZE) -> int:
    """Xoá PendingShipmentScan trạng thái MATCHED cũ hơn
    PENDING_SHIPMENT_SCAN_RETENTION_DAYS (env, default 90 ngày).

    CHỈ xoá dòng ĐÃ khớp — pending/failed/invalid/cancelled KHÔNG BAO GIỜ bị task
    này đụng tới (còn cần người xử lý tay / cron thử lại). Cutoff dựa trên
    ``created_at`` (tận dụng index ``pendingscan_status_created`` sẵn có); dòng
    matched hầu như không bị sửa sau khi khớp nên created_at ≈ updated_at.
    """
    from apps.inventory.models import (
        PendingScanKind,
        PendingScanStatus,
        PendingShipmentScan,
    )

    retention_days = int(getattr(settings, "PENDING_SHIPMENT_SCAN_RETENTION_DAYS", 90))
    if retention_days <= 0:
        logger.info(
            "pending scan purge disabled (PENDING_SHIPMENT_SCAN_RETENTION_DAYS<=0)"
        )
        return 0

    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted_total = 0
    with single_flight(_PENDING_SCAN_PURGE_LOCK_KEY, _PENDING_SCAN_PURGE_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("purge_matched_pending_scans already running — skip this cycle")
            return -1
        while True:
            ids = list(
                PendingShipmentScan.objects.filter(
                    status=PendingScanStatus.MATCHED, created_at__lt=cutoff,
                )
                # Giữ lại dòng còn nghi ngờ "hàng gửi bù chưa có phiếu xuất"
                # (cùng luật với `scan_needs_replacement_issue`): đây là một
                # quyết định NGƯỜI chưa xử lý — purge là xoá luôn câu hỏi.
                .exclude(
                    kind=PendingScanKind.DISPATCH,
                    error__contains="already_dispatched",
                    result_ref="",
                )
                .order_by()
                .values_list("id", flat=True)[:batch_size]
            )
            if not ids:
                break
            deleted, _ = PendingShipmentScan.objects.filter(id__in=ids).delete()
            deleted_total += deleted
    logger.info(
        "purged %s matched pending scans older than %s days", deleted_total, retention_days
    )
    return deleted_total


# Ngưỡng nhắc: đơn huỷ đã xuất kho mà hàng chưa về sau ngần này ngày.
AWAITING_RETURN_OVERDUE_DAYS = 14
# Trần dòng trong một tin Lark — danh sách dài hơn thì cắt và nói rõ còn bao nhiêu.
_AWAITING_RETURN_ALERT_LIMIT = 30


@shared_task(
    name="apps.inventory.tasks.alert_awaiting_return_overdue",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
    soft_time_limit=100,
    time_limit=120,
)
def alert_awaiting_return_overdue() -> int:
    """Nhắc mỗi ngày: đơn huỷ đã xuất kho mà hàng chưa về sau 14 ngày.

    KHÔNG tự đóng đơn — quyết định coi như mất hàng phải có người chịu trách nhiệm
    (spec 2026-07-29-cancelled-after-dispatch-return-design.md §5). Task chỉ đẩy
    danh sách lên Lark để có người xử lý trên tab "Chờ hàng hoàn về".
    """
    from apps.inventory.models import CancelledDispatchWatch, WatchStatus

    now = timezone.now()
    cutoff = now - timedelta(days=AWAITING_RETURN_OVERDUE_DAYS)
    overdue = CancelledDispatchWatch.objects.select_related("order", "warehouse").filter(
        status=WatchStatus.WAITING, cancelled_at__lte=cutoff
    )
    total = overdue.count()
    if not total:
        return 0

    rows = list(overdue.order_by("cancelled_at")[:_AWAITING_RETURN_ALERT_LIMIT])
    lines = "\n".join(
        f"• {watch.order.shipping_code or watch.order.order_code or f'#{watch.order_id}'} "
        f"— kho {watch.warehouse.name} — chờ {(now - watch.cancelled_at).days} ngày"
        for watch in rows
    )
    if total > len(rows):
        lines += f"\n… và {total - len(rows)} đơn nữa."
    send_stock_negative_alert(
        f"📦 {total} đơn huỷ đã xuất kho quá {AWAITING_RETURN_OVERDUE_DAYS} ngày "
        f"chưa thấy hàng về:\n{lines}"
    )
    logger.info("Cảnh báo %s đơn huỷ chờ hàng về quá hạn", total)
    return total
