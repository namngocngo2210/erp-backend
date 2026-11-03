"""Celery tasks của CNV Loyalty sync — tất cả route queue `slow` (xem
config/settings_parts/celery.py): gọi API ngoài, không được tranh slot với
đường tạo đơn.

Task sync đơn lẻ KHÔNG raise/retry qua Celery — thất bại nằm ở CnvSyncRecord,
cron ``retry_failed_records`` quét lại với backoff (spec §3.7).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.db import OperationalError
from django.conf import settings
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

_RETRY_LOCK_KEY = "lock:cnv_retry_failed_records"
_RETRY_LOCK_TTL = 1800  # > time_limit
_RETRY_BATCH = 500
_BACKOFF_CAP_MINUTES = 360  # 6h

_RECONCILE_LOCK_KEY = "lock:cnv_reconcile_full"
_RECONCILE_LOCK_TTL = 2400
_RECONCILE_CAP_PER_ENTITY = 2000

_BACKFILL_LOCK_KEY = "lock:cnv_run_backfill"
_BACKFILL_LOCK_TTL = 21900  # > time_limit 6h của run_backfill


def _build_service(require_enabled: bool = True):
    """Trả về CnvSyncService gắn client thật, hoặc None khi chưa bật/chưa kết nối.

    ``require_enabled=False`` cho các đường CHẠY TAY có chủ đích (backfill): cờ
    ``CNV_SYNC_ENABLED`` gate luồng TỰ ĐỘNG (signal, cron), không phải lệnh mà
    người vận hành cố ý gõ — nhờ vậy đổ dữ liệu có kiểm soát được trước khi bật
    sync realtime.
    """
    from apps.cnv_loyalty.application.services.sync_service import CnvSyncService
    from apps.cnv_loyalty.infrastructure.cnv_client import CnvLoyaltyClient
    from apps.cnv_loyalty.models import CnvConnection

    if require_enabled and not sysconfig.get("CNV_SYNC_ENABLED"):
        return None
    connection = CnvConnection.get_active()
    if connection is None:
        logger.warning("CNV sync: chưa có CnvConnection active — bỏ qua")
        return None
    return CnvSyncService(CnvLoyaltyClient(connection))


@shared_task(name="apps.cnv_loyalty.tasks.sync_customer", soft_time_limit=60, time_limit=90, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=60, max_retries=3)
def sync_customer(customer_id: int) -> str:
    service = _build_service()
    if service is None:
        return "disabled"
    return service.sync_customer(customer_id).status


@shared_task(name="apps.cnv_loyalty.tasks.sync_product", soft_time_limit=60, time_limit=90, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=60, max_retries=3)
def sync_product(product_id: int) -> str:
    service = _build_service()
    if service is None:
        return "disabled"
    return service.sync_product(product_id).status


@shared_task(name="apps.cnv_loyalty.tasks.sync_collection", soft_time_limit=60, time_limit=90, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=60, max_retries=3)
def sync_collection(category_id: int) -> str:
    service = _build_service()
    if service is None:
        return "disabled"
    return service.sync_collection(category_id).status


@shared_task(name="apps.cnv_loyalty.tasks.sync_order", soft_time_limit=120, time_limit=150, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=60, max_retries=3)
def sync_order(order_id: int) -> str:
    # Limit cao hơn task khác: một đơn có thể kéo theo sync customer + N product.
    service = _build_service()
    if service is None:
        return "disabled"
    return service.sync_order(order_id).status


_SYNC_TASKS = {
    "customer": sync_customer,
    "product": sync_product,
    "custom_collection": sync_collection,
    "order": sync_order,
}


@shared_task(
    name="apps.cnv_loyalty.tasks.retry_failed_records",
    soft_time_limit=1500,
    time_limit=1740,
)
def retry_failed_records(max_records: int = _RETRY_BATCH) -> dict:
    """Re-dispatch record chưa synced, backoff 2^attempts phút (trần 6h, 10 lần).

    Lấy theo ``updated_at`` cũ nhất trước rồi lọc backoff trong Python — điều
    kiện backoff phụ thuộc từng row (attempts), không diễn đạt gọn bằng SQL.
    """
    from apps.cnv_loyalty.models import CnvSyncRecord, CnvSyncStatus

    if not sysconfig.get("CNV_SYNC_ENABLED"):
        return {"dispatched": 0, "reason": "disabled"}

    max_attempts = int(getattr(settings, "CNV_SYNC_MAX_ATTEMPTS", 10))
    now = timezone.now()
    dispatched = 0
    with single_flight(_RETRY_LOCK_KEY, _RETRY_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("cnv retry_failed_records đang chạy — skip")
            return {"dispatched": -1}
        candidates = (
            CnvSyncRecord.objects.exclude(status=CnvSyncStatus.SYNCED)
            .filter(attempts__lt=max_attempts)
            .order_by("updated_at")[:max_records]
        )
        for record in candidates:
            backoff = timedelta(
                minutes=min(2 ** record.attempts, _BACKOFF_CAP_MINUTES)
            )
            if record.updated_at + backoff > now:
                continue
            task = _SYNC_TASKS.get(record.entity_type)
            if task is None:
                continue
            task.delay(record.local_id)
            dispatched += 1
    logger.info("cnv retry_failed_records: dispatched=%s", dispatched)
    return {"dispatched": dispatched}


@shared_task(
    name="apps.cnv_loyalty.tasks.reconcile_full",
    soft_time_limit=1500,
    time_limit=1740,
)
def reconcile_full(cap_per_entity: int = _RECONCILE_CAP_PER_ENTITY) -> dict:
    """Lưới cuối hằng đêm: vớt entity CHƯA CÓ record (đường nào lọt trigger).

    Cap mỗi entity mỗi đêm — có cap là có truncation, nên log rõ số còn lại;
    tồn đọng lớn (go-live) xử lý bằng lệnh ``cnv_backfill``, không phải cron này.
    """
    from apps.cnv_loyalty.models import CnvEntityType, CnvSyncRecord
    from apps.customers.models import Customer
    from apps.orders.models import Order
    from apps.catalog.models import Product, ProductCategory

    if not sysconfig.get("CNV_SYNC_ENABLED"):
        return {"reason": "disabled"}

    plans = [
        (CnvEntityType.CUSTOM_COLLECTION, ProductCategory.objects, sync_collection),
        (CnvEntityType.PRODUCT, Product.objects, sync_product),
        (CnvEntityType.CUSTOMER, Customer.objects, sync_customer),
        (CnvEntityType.ORDER, Order.objects, sync_order),
    ]
    stats: dict[str, int] = {}
    with single_flight(_RECONCILE_LOCK_KEY, _RECONCILE_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("cnv reconcile_full đang chạy — skip")
            return {"reason": "locked"}
        for entity_type, manager, task in plans:
            tracked = CnvSyncRecord.objects.filter(entity_type=entity_type).values("local_id")
            missing = list(
                manager.filter(deleted_at__isnull=True)
                .exclude(id__in=tracked)
                .order_by("id")
                .values_list("id", flat=True)[: cap_per_entity + 1]
            )
            overflow = len(missing) > cap_per_entity
            for pk in missing[:cap_per_entity]:
                task.delay(pk)
            stats[entity_type] = min(len(missing), cap_per_entity)
            if overflow:
                logger.warning(
                    "cnv reconcile: %s còn entity thiếu record vượt cap %s/đêm — "
                    "chạy `manage.py cnv_backfill` nếu tồn đọng lớn",
                    entity_type, cap_per_entity,
                )
    logger.info("cnv reconcile_full: %s", stats)
    return stats


@shared_task(
    name="apps.cnv_loyalty.tasks.run_backfill",
    soft_time_limit=21000,
    time_limit=21600,
)
def run_backfill(
    entities: list[str] | None = None,
    since: str | None = None,
    batch_size: int = 200,
) -> dict:
    """Backfill toàn bộ (chạy từ command ``cnv_backfill``). Lock 6h.

    Chạy được cả khi CNV_SYNC_ENABLED=0 — xem docstring ``_build_service``.
    """
    from apps.cnv_loyalty.application.services.backfill_service import CnvBackfillService

    service = _build_service(require_enabled=False)
    if service is None:
        return {"reason": "no_connection"}
    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)
        if timezone.is_naive(since_dt):
            since_dt = timezone.make_aware(since_dt)
    with single_flight(_BACKFILL_LOCK_KEY, _BACKFILL_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("cnv run_backfill đang chạy — skip")
            return {"reason": "locked"}
        return CnvBackfillService(service, batch_size=batch_size).run(
            entities=entities, since=since_dt
        )


# ─────────────────────────────────────────────────────────────────────────────
# Chiều NGƯỢC: CNV → desi-order (spec 2026-08-19)
# ─────────────────────────────────────────────────────────────────────────────

_INBOUND_LOCK_KEY = "lock:cnv_poll_inbound_orders"
_INBOUND_LOCK_TTL = 1800
_INBOUND_CURSOR_RESOURCE = "orders"
_WEBHOOK_RETRY_BATCH = 200
_WEBHOOK_MAX_ATTEMPTS = 10


def _build_inbound_service():
    """CnvOrderIngestService gắn client thật, hoặc None khi chưa bật/chưa kết nối.

    Cờ riêng ``CNV_INBOUND_ENABLED`` chứ không dùng chung ``CNV_SYNC_ENABLED``:
    đẩy lên đang chạy ổn không có nghĩa là sẵn sàng nhận về, và ngược lại.
    """
    from apps.cnv_loyalty.application.services.inbound_service import CnvOrderIngestService
    from apps.cnv_loyalty.infrastructure.cnv_client import CnvLoyaltyClient
    from apps.cnv_loyalty.models import CnvConnection

    if not getattr(settings, "CNV_INBOUND_ENABLED", False):
        return None
    connection = CnvConnection.get_active()
    if connection is None:
        logger.warning("CNV inbound: chưa có CnvConnection active — bỏ qua")
        return None
    return CnvOrderIngestService(CnvLoyaltyClient(connection))


@shared_task(
    name="apps.cnv_loyalty.tasks.poll_inbound_orders",
    soft_time_limit=1500,
    time_limit=1740,
)
def poll_inbound_orders() -> dict:
    """Quét đơn CNV theo cửa sổ ``updated_at``. LƯỚI VÁ cho webhook, không phải
    đường chính — webhook mới là.

    Cửa sổ luôn lùi lại ``CNV_INBOUND_LOOKBACK_MINUTES``: quét chồng lặp vô hại
    (``cnv_order_id`` unique nên upsert), còn bỏ sót đơn thì không ai biết.
    """
    from apps.cnv_loyalty.models import CnvInboundCursor

    service = _build_inbound_service()
    if service is None:
        return {"reason": "disabled"}

    lookback = timedelta(minutes=int(getattr(settings, "CNV_INBOUND_LOOKBACK_MINUTES", 120)))
    now = timezone.now()
    with single_flight(_INBOUND_LOCK_KEY, _INBOUND_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("cnv poll_inbound_orders đang chạy — skip")
            return {"reason": "locked"}
        cursor, _created = CnvInboundCursor.objects.get_or_create(
            resource=_INBOUND_CURSOR_RESOURCE,
            defaults={"synced_until": now - lookback},
        )
        window_from = min(cursor.synced_until, now) - lookback
        try:
            stats = service.poll(window_from, now)
        except Exception as exc:  # noqa: BLE001 — cursor KHÔNG tiến, lần sau quét lại
            cursor.last_run_at = now
            cursor.last_error = str(exc)[:1000]
            cursor.save(update_fields=["last_run_at", "last_error"])
            logger.exception("CNV poll_inbound_orders lỗi")
            return {"reason": "error", "error": str(exc)[:200]}
        # Chỉ tiến cursor khi lô chạy trót lọt. Có đơn lỗi lẻ (stats.failed) vẫn
        # tiến: cửa sổ sau vẫn phủ lại nhờ lookback, còn đứng yên vì một đơn hỏng
        # thì cursor kẹt vĩnh viễn và mỗi lần chạy lại quét từ đầu.
        cursor.synced_until = now
        cursor.last_run_at = now
        cursor.last_error = "; ".join(stats.errors)[:1000]
        cursor.save(update_fields=["synced_until", "last_run_at", "last_error"])
    result = stats.as_dict()
    logger.info("cnv poll_inbound_orders: %s", result)
    return result


@shared_task(
    name="apps.cnv_loyalty.tasks.retry_unprocessed_webhook_events",
    soft_time_limit=600,
    time_limit=720,
)
def retry_unprocessed_webhook_events(max_records: int = _WEBHOOK_RETRY_BATCH) -> dict:
    """Áp lại event webhook chưa xử lý được (CNV đã nhận 200, không gửi lại nữa).

    Không backoff theo cấp số nhân như outbox đẩy đi: event ở đây thường fail vì
    một lỗi tạm (DB bận, đơn tham chiếu chưa có) chứ không phải vì CNV từ chối,
    nên thử lại đều đặn là đủ. ``attempts`` vẫn chặn retry vô hạn.
    """
    from apps.cnv_loyalty.application.services.webhook_service import apply_event
    from apps.cnv_loyalty.models import CnvWebhookEvent

    if not getattr(settings, "CNV_INBOUND_ENABLED", False):
        return {"applied": 0, "reason": "disabled"}

    events = (
        CnvWebhookEvent.objects.filter(processed=False, attempts__lt=_WEBHOOK_MAX_ATTEMPTS)
        .order_by("received_at")[:max_records]
    )
    applied = 0
    for event in events:
        try:
            if apply_event(event):
                applied += 1
        except Exception:  # noqa: BLE001 — một event hỏng không chặn cả lô
            logger.exception("CNV webhook retry: event#%s lỗi", event.pk)
    logger.info("cnv retry_unprocessed_webhook_events: applied=%s", applied)
    return {"applied": applied}


@shared_task(
    name="apps.cnv_loyalty.tasks.purge_old_webhook_events",
    soft_time_limit=600,
    time_limit=720,
)
def purge_old_webhook_events() -> dict:
    """Xoá event ĐÃ XỬ LÝ cũ hơn ngưỡng retention.

    Chỉ xoá `processed=True`: event chưa xử lý là bằng chứng còn dùng để phá án,
    xoá theo tuổi là mất manh mối đúng lúc cần nhất.
    """
    from apps.cnv_loyalty.models import CnvWebhookEvent

    days = int(getattr(settings, "CNV_WEBHOOK_EVENT_RETENTION_DAYS", 30))
    threshold = timezone.now() - timedelta(days=days)
    deleted, _ = CnvWebhookEvent.objects.filter(
        processed=True, received_at__lt=threshold
    ).delete()
    logger.info("cnv purge_old_webhook_events: deleted=%s (>%s ngày)", deleted, days)
    return {"deleted": deleted}
