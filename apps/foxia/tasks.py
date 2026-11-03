"""Celery task của Foxia — route vào queue `foxia` (config/settings_parts/celery.py).

Không dùng queue `default`: gọi API ngoài là I/O chậm, chiếm slot của đường tạo
đơn. Không dùng chung `slow` làm queue khai báo vì backfill Foxia có thể chạy
hàng chục phút. Từ spec 2026-09-09, queue `foxia` do container RIÊNG
`worker-foxia` tiêu thụ (kèm `foxia_push` của chiều đẩy đơn) — `worker-slow` đã
nhả queue này.

`CELERY_TASK_TIME_LIMIT` toàn cục là 30 GIÂY — mọi task ở đây bắt buộc override,
nếu không sẽ bị giết giữa chừng ngay lượt đầu.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import OperationalError
from django.conf import settings

from apps.common.db_guards import single_flight

logger = logging.getLogger(__name__)

_CATALOG_LOCK_KEY = "lock:foxia_sync_catalog"
_CATALOG_LOCK_TTL = 1800  # > time_limit
_ORDERS_LOCK_TTL = 1800
_LEAD_MATCH_LOCK_KEY = "lock:foxia_match_lead_orders"
_LEAD_MATCH_LOCK_TTL = 300  # > time_limit
_RESALE_LOCK_KEY = "lock:foxia_push_resale_delivered"
_RESALE_LOCK_TTL = 900  # > time_limit


def build_client(require_enabled: bool = True):
    """Trả FoxiaClient gắn connection active, hoặc None khi chưa bật/chưa cấu hình.

    `require_enabled=False` cho các đường CHẠY TAY có chủ đích (probe, backfill):
    `FOXIA_SYNC_ENABLED` gate luồng TỰ ĐỘNG, không phải lệnh người vận hành cố ý
    gõ — nhờ vậy thử kết nối được trước khi bật cron.
    """
    from apps.foxia.infrastructure.foxia_client import FoxiaClient
    from apps.foxia.models import FoxiaConnection

    if require_enabled and not getattr(settings, "FOXIA_SYNC_ENABLED", False):
        return None
    connection = FoxiaConnection.get_active()
    if connection is None:
        logger.warning("Foxia sync: chưa có FoxiaConnection active — bỏ qua")
        return None
    return FoxiaClient(connection)


@shared_task(
    name="apps.foxia.tasks.sync_catalog",
    soft_time_limit=840,
    time_limit=900,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def sync_catalog() -> str:
    """Đồng bộ toàn bộ danh mục. Chạy 1 lần/giờ — danh mục đổi rất chậm."""
    from apps.foxia.application.services.catalog_sync_service import FoxiaCatalogSyncService

    with single_flight(_CATALOG_LOCK_KEY, _CATALOG_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Foxia sync_catalog: lượt trước chưa xong — bỏ qua lượt này")
            return "skipped"

        client = build_client()
        if client is None:
            return "disabled"

        results = FoxiaCatalogSyncService(client).sync_all()
        failed = [r.resource for r in results if not r.ok]
        total = sum(r.written for r in results)
        if failed:
            logger.warning("Foxia sync_catalog: %s danh mục lỗi: %s", len(failed), failed)
            return f"partial:{total}"
        return f"ok:{total}"


def _sync_orders(mode: str) -> str:
    from apps.foxia.application.services.order_sync_service import FoxiaOrderSyncService

    with single_flight(f"lock:foxia_{mode}", _ORDERS_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Foxia %s: lượt trước chưa xong — bỏ qua lượt này", mode)
            return "skipped"

        client = build_client()
        if client is None:
            return "disabled"

        result = FoxiaOrderSyncService(client).sync(mode)
        return f"ok:{result.created}+{result.updated}"


@shared_task(name="apps.foxia.tasks.sync_orders", soft_time_limit=840, time_limit=900, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=60, max_retries=3)
def sync_orders() -> str:
    """Đơn mới/vừa chốt, lọc theo `ngayxacnhan`. Nhịp 20 phút."""
    from apps.foxia.domain.order_specs import OrderSyncMode

    return _sync_orders(OrderSyncMode.CONFIRMED)


@shared_task(name="apps.foxia.tasks.sync_delivered", soft_time_limit=840, time_limit=900, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=60, max_retries=3)
def sync_delivered() -> str:
    """Đơn giao thành công, lọc theo `ngayhoanthanh`, cửa sổ dài hơn.

    Lượt RIÊNG vì đơn chốt tháng 7 có thể giao xong tháng 8 — lọc theo
    `ngayxacnhan` sẽ không bao giờ thấy nó đổi trạng thái.
    """
    from apps.foxia.domain.order_specs import OrderSyncMode

    return _sync_orders(OrderSyncMode.DELIVERED)


@shared_task(
    name="apps.foxia.tasks.push_order_to_foxia",
    soft_time_limit=25,
    time_limit=30,
    max_retries=0,
)
def push_order_to_foxia(order_id: int) -> str:
    """Đẩy khách của MỘT đơn landing sang Foxia. Thử đúng một lần.

    Không override time limit lên cao như các task sync: đây là một request HTTP
    ngắn, trần mặc định 30s của CELERY_TASK_TIME_LIMIT là đủ.

    `max_retries=0` là quyết định nghiệp vụ, không phải thiếu sót: POST này KHÔNG
    idempotent (Foxia chưa cấp khoá chống trùng), retry tự động = khách thứ hai.
    """
    from apps.foxia.application.services.order_push_service import FoxiaOrderPushService

    return FoxiaOrderPushService.push(order_id)


@shared_task(
    name="apps.foxia.tasks.match_lead_orders",
    soft_time_limit=110,
    time_limit=120,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def match_lead_orders() -> str:
    """Điền `foxia_order_id` cho cơ hội đã lên đơn. Nhịp 15 phút.

    KHÔNG gọi Foxia — chỉ join `foxia_lead_links` với `foxia_order_links` mà
    `sync_orders` đã kéo về, nên không cần `build_client()` và chạy được cả khi
    `FOXIA_SYNC_ENABLED=0` (lúc đó đơn giản là không có gì mới để khớp).
    """
    from apps.foxia.application.services.lead_match_service import FoxiaLeadMatchService

    with single_flight(_LEAD_MATCH_LOCK_KEY, _LEAD_MATCH_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Foxia match_lead_orders: lượt trước chưa xong — bỏ qua lượt này")
            return "skipped"

        return f"ok:{FoxiaLeadMatchService.match()}"


@shared_task(
    name="apps.foxia.tasks.push_resale_delivered",
    soft_time_limit=540,
    time_limit=600,
    max_retries=0,
)
def push_resale_delivered() -> str:
    """Đơn giao thành công → cơ hội resale bên Foxia. Nhịp 1 phút.

    `max_retries=0` cùng lý do `push_order_to_foxia`: POST không idempotent. Lượt
    sau tự nhặt phần chưa làm (dòng chưa ghi), không bao giờ đẩy lại dòng đã ghi.
    Tự no-op khi FOXIA_PUSH_ENABLED hoặc FOXIA_RESALE_PUSH_ENABLED tắt.
    """
    from apps.foxia.application.services.resale_push_service import FoxiaResalePushService

    with single_flight(_RESALE_LOCK_KEY, _RESALE_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Foxia push_resale_delivered: lượt trước chưa xong — bỏ qua lượt này")
            return "skipped"

        return FoxiaResalePushService().run().summary()
