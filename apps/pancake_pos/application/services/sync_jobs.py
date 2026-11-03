"""Điều phối lượt đồng bộ chạy nền (Celery) — khoá, hàng đợi dirty, nhật ký.

Một khoá duy nhất (`single_flight` Redis) cho MỌI lượt ghi liên kết: full, dry-run,
tăng dần. Hai lượt chồng nhau sẽ đọc cùng ảnh chụp liên kết rồi cùng tạo sản phẩm
Pancake → trùng. Ngoài ra, lượt chạy tay (API) được chặn sớm bằng kiểm tra "còn
lượt full/dry-run chưa xong" trong `pg_advisory_xact_lock` để hai cú bấm đồng thời
không cùng qua cửa (trả 409 thay vì tạo 2 dòng nhật ký).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.pancake_pos.application.services.factory import build_product_sync_service
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError
from apps.pancake_pos.models import PancakePosSyncRun, SyncRunMode
from apps.pancake_pos.repositories import DirtyQueue, LinkRepository
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

LOCK_KEY = "lock:pancake_pos_sync"
# > time_limit của task full (3600s) — van an toàn khi worker chết giữa chừng.
LOCK_TTL_SECONDS = 3900
# Khoá advisory (bigint tuỳ chọn, cố định) cho đoạn kiểm-tra-rồi-tạo lượt chạy tay.
_ADVISORY_KEY = 7_026_092_901
INCREMENTAL_BATCH = 200

MANUAL_MODES = (SyncRunMode.FULL, SyncRunMode.DRY_RUN)


class SyncAlreadyRunning(Exception):
    """Đang có lượt full/dry-run chưa xong."""


def active_manual_run() -> PancakePosSyncRun | None:
    """Lượt chạy tay chưa xong và chưa quá hạn khoá (quá hạn = worker đã chết)."""
    since = timezone.now() - timedelta(seconds=LOCK_TTL_SECONDS)
    return (
        PancakePosSyncRun.objects.filter(mode__in=MANUAL_MODES, finished_at__isnull=True, started_at__gte=since)
        .order_by("-started_at")
        .first()
    )


def create_manual_run(mode: str, user_id: int | None) -> int:
    """Tạo dòng nhật ký cho lượt chạy tay; có lượt khác đang chạy → SyncAlreadyRunning."""
    if mode not in MANUAL_MODES:
        raise ValueError(f"mode không hợp lệ: {mode}")
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [_ADVISORY_KEY])
        if active_manual_run() is not None:
            raise SyncAlreadyRunning("Đang có lượt đồng bộ khác chưa xong")
        return LinkRepository.start_run(mode, user_id)


def fail_run(run_id: int, message: str) -> None:
    """Đóng dòng nhật ký với lỗi chặn cả lượt (không chạy được)."""
    PancakePosSyncRun.objects.filter(id=run_id, finished_at__isnull=True).update(
        finished_at=timezone.now(), report={"fatal": message}
    )


def execute_manual_run(
    run_id: int,
    *,
    mode: str,
    limit: int | None = None,
    skus: Sequence[str] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Chạy lượt full/dry-run đã tạo sẵn. `{"reason": "locked"}` = chưa giành được
    khoá (task tự thử lại); lỗi cấu hình → đóng nhật ký với `fatal`."""
    dry_run = mode == SyncRunMode.DRY_RUN
    with single_flight(LOCK_KEY, LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            return {"reason": "locked"}
        try:
            service = build_product_sync_service(dry_run=dry_run)
        except PancakePosError as exc:
            fail_run(run_id, str(exc))
            return {"reason": "error", "detail": str(exc)}
        try:
            report = service.full_sync(dry_run=dry_run, limit=limit, skus=skus or None, user_id=user_id, run_id=run_id)
        except PancakePosError as exc:
            # Service đã đóng nhật ký với `fatal`; không raise để Celery không retry lượt ghi.
            return {"reason": "error", "detail": str(exc)}
        return {"run_id": report.run_id, **report.counters.as_dict()}


def process_dirty(batch: int = INCREMENTAL_BATCH) -> dict[str, Any]:
    """Beat 2 phút: lấy tối đa `batch` sản phẩm dirty → `sync_products`. Chỉ chạy khi
    PANCAKE_POS_ENABLED. Lỗi chặn cả lượt → trả id về hàng đợi cho lượt sau."""
    if not sysconfig.get("PANCAKE_POS_ENABLED"):
        return {"reason": "disabled"}
    with single_flight(LOCK_KEY, LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            return {"reason": "locked"}
        ids = DirtyQueue.claim(batch)
        if not ids:
            return {"reason": "empty"}
        try:
            report = build_product_sync_service(dry_run=False).sync_products(ids)
        except PancakePosError as exc:
            # Lỗi phía Pancake/cấu hình (key sai, mạng…): nhật ký đã có `fatal`.
            DirtyQueue.mark(ids)
            logger.warning("pancake_pos: lượt tăng dần lỗi (%s) — trả %d id về hàng đợi", exc, len(ids))
            return {"reason": "error", "detail": str(exc), "products": len(ids)}
        except Exception:
            DirtyQueue.mark(ids)
            logger.exception("pancake_pos: lượt tăng dần lỗi — trả %d id về hàng đợi", len(ids))
            raise
        return {"run_id": report.run_id, "products": len(ids), **report.counters.as_dict()}
