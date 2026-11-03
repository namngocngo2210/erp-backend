"""Celery task của đồng bộ Pancake POS — route queue `foxia` (I/O đối tác, chung
worker với poll Pancake; xem config/settings_parts/celery.py).

- `run_sync`: lượt full/dry-run bấm từ UI/API (dòng nhật ký tạo sẵn). Đụng khoá
  với lượt tăng dần đang chạy → tự thử lại sau 30s (lượt tăng dần chỉ vài giây).
- `sync_dirty_products`: beat 2 phút, xử lý hàng đợi dirty theo lô.
- `run_initial_stock`: lượt đẩy tồn đầu kỳ (dry-run/ghi) bấm từ UI (two-way-sync A1).
- `push_orders`: beat 2 phút — quét bù + đẩy đơn + đối chiếu sửa/huỷ (two-way-sync A2).
- `push_orders_for`: đơn cụ thể (event chốt/đổi trạng thái, chọn cách xuất, thử lại).
- `process_webhook_event`: một sự kiện webhook `orders` (xếp sau khi lưu thô, two-way-sync B1).
- `process_webhook_events`: beat 1 phút — vớt sự kiện còn chờ/lỗi, khớp lại đơn chờ, dọn cũ.
  Hai task webhook chỉ chạm DB (không gọi Pancake) → queue mặc định, không route `foxia`.
- `sync_delivery_code`: đơn CRM vừa áp sự kiện Pancake → "Trạng thái vận chuyển thực" (MR D;
  có thể xuất kho khi bật PANCAKE_POS_DISPATCH_ENABLED — HTTP KiotViet, không gọi Pancake).
- `alert_stuck_deliveries`: beat mỗi giờ, Lark báo đơn treo một lần/ngày đúng giờ cấu hình.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from apps.pancake_pos.application.services import (
    delivery_jobs,
    initial_stock_jobs,
    order_push_jobs,
    sync_jobs,
    webhook_jobs,
)

logger = logging.getLogger(__name__)

LOCKED_RETRY_COUNTDOWN = 30
LOCKED_MAX_RETRIES = 20


@shared_task(
    bind=True,
    name="apps.pancake_pos.tasks.run_sync",
    soft_time_limit=3300,
    time_limit=3600,
    max_retries=LOCKED_MAX_RETRIES,
)
def run_sync(
    self,
    run_id: int,
    mode: str,
    limit: int | None = None,
    skus: list[str] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    result = sync_jobs.execute_manual_run(run_id, mode=mode, limit=limit, skus=skus, user_id=user_id)
    if result.get("reason") == "locked":
        if self.request.retries >= LOCKED_MAX_RETRIES:
            sync_jobs.fail_run(run_id, "Không giành được khoá — đang có lượt đồng bộ khác chạy quá lâu")
            return result
        raise self.retry(countdown=LOCKED_RETRY_COUNTDOWN)
    return result


@shared_task(
    name="apps.pancake_pos.tasks.sync_dirty_products",
    soft_time_limit=540,
    time_limit=600,
    max_retries=0,
)
def sync_dirty_products(batch: int = sync_jobs.INCREMENTAL_BATCH) -> dict[str, Any]:
    return sync_jobs.process_dirty(batch)


@shared_task(
    bind=True,
    name="apps.pancake_pos.tasks.run_initial_stock",
    soft_time_limit=1700,
    time_limit=1800,
    max_retries=LOCKED_MAX_RETRIES,
)
def run_initial_stock(
    self,
    run_id: int,
    dry_run: bool,
    warehouse_ids: list[int] | None = None,
    skus: list[str] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    result = initial_stock_jobs.execute_run(
        run_id, dry_run=dry_run, warehouse_ids=warehouse_ids, skus=skus, user_id=user_id
    )
    if result.get("reason") == "locked":
        if self.request.retries >= LOCKED_MAX_RETRIES:
            sync_jobs.fail_run(run_id, "Không giành được khoá — đang có lượt tồn đầu kỳ khác chạy quá lâu")
            return result
        raise self.retry(countdown=LOCKED_RETRY_COUNTDOWN)
    return result


@shared_task(
    name="apps.pancake_pos.tasks.push_orders",
    soft_time_limit=500,
    time_limit=540,
    max_retries=0,
)
def push_orders() -> dict[str, Any]:
    return order_push_jobs.run_cycle()


@shared_task(
    name="apps.pancake_pos.tasks.push_orders_for",
    soft_time_limit=240,
    time_limit=300,
    max_retries=0,
)
def push_orders_for(order_ids: list[int], manual: bool = False, force: bool = False) -> dict[str, Any]:
    return order_push_jobs.run_for_orders(order_ids, manual=manual, force=force)


@shared_task(
    name="apps.pancake_pos.tasks.process_webhook_event",
    soft_time_limit=60,
    time_limit=90,
    max_retries=0,
)
def process_webhook_event(event_id: int) -> str:
    return webhook_jobs.process_one(event_id)


@shared_task(
    name="apps.pancake_pos.tasks.process_webhook_events",
    soft_time_limit=240,
    time_limit=280,
    max_retries=0,
)
def process_webhook_events() -> dict[str, Any]:
    return webhook_jobs.run_cycle()


@shared_task(
    name="apps.pancake_pos.tasks.sync_delivery_code",
    bind=True,
    soft_time_limit=60,
    time_limit=90,
    max_retries=3,
)
def sync_delivery_code(self, order_id: int) -> str:
    """Đồng bộ "Trạng thái vận chuyển thực" của một đơn CRM theo Pancake (MR D). Xuất kho lỗi
    tạm (KiotViet chưa xác minh được) → thử lại sau 5 phút, tối đa 3 lần; còn lại nằm ở báo
    cáo lệch."""
    outcome = delivery_jobs.sync_one(int(order_id))
    if outcome.result == "error" and outcome.detail == "verify_unavailable" and self.request.retries < self.max_retries:
        raise self.retry(countdown=300)
    return str(outcome)


@shared_task(
    name="apps.pancake_pos.tasks.alert_stuck_deliveries",
    soft_time_limit=60,
    time_limit=90,
    max_retries=0,
)
def alert_stuck_deliveries() -> bool:
    """Beat mỗi giờ; chỉ gửi Lark đúng giờ cấu hình, một lần/ngày (MR D)."""
    return delivery_jobs.alert_stuck()
