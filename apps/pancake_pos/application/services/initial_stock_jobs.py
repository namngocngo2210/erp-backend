"""Điều phối lượt đẩy tồn đầu kỳ chạy nền (two-way-sync §7 A1).

- Khoá riêng (`single_flight`) — không dùng chung khoá đồng bộ sản phẩm: lượt tồn
  không ghi bảng liên kết, chỉ đọc.
- Lượt chạy tay qua API chặn sớm bằng `pg_advisory_xact_lock` + kiểm tra "còn lượt
  tồn đầu kỳ chưa xong" (409), như `sync_jobs`.
- Ghi thật từ UI bắt buộc có dry-run THÀNH CÔNG GẦN NHẤT ≤ 30 phút; phạm vi (kho,
  SKU) của lượt ghi lấy từ chính lượt dry-run đó → người dùng ghi đúng cái đã xem.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.pancake_pos.application.services.factory import build_initial_stock_service
from apps.pancake_pos.application.services.sync_jobs import fail_run
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError
from apps.pancake_pos.models import PancakePosSyncRun, SyncRunMode
from apps.pancake_pos.repositories import LinkRepository

LOCK_KEY = "lock:pancake_pos_initial_stock"
LOCK_TTL_SECONDS = 1900  # > time_limit task (1800s)
_ADVISORY_KEY = 7_026_093_001
DRY_RUN_MAX_AGE = timedelta(minutes=30)

MODES = (SyncRunMode.INITIAL_STOCK_DRY_RUN, SyncRunMode.INITIAL_STOCK)


class InitialStockAlreadyRunning(Exception):
    """Đang có lượt tồn đầu kỳ chưa xong."""


class DryRunRequired(Exception):
    """Chưa có dry-run hợp lệ để cho phép ghi."""


def active_run() -> PancakePosSyncRun | None:
    since = timezone.now() - timedelta(seconds=LOCK_TTL_SECONDS)
    return (
        PancakePosSyncRun.objects.filter(mode__in=MODES, finished_at__isnull=True, started_at__gte=since)
        .order_by("-started_at")
        .first()
    )


def latest_dry_run() -> PancakePosSyncRun | None:
    """Lượt dry-run tồn đầu kỳ đã xong gần nhất (kể cả lỗi — để UI báo lý do)."""
    return (
        PancakePosSyncRun.objects.filter(mode=SyncRunMode.INITIAL_STOCK_DRY_RUN, finished_at__isnull=False)
        .order_by("-finished_at", "-id")
        .first()
    )


def dry_run_ok(run: PancakePosSyncRun | None, *, now: datetime | None = None) -> bool:
    if run is None or run.finished_at is None:
        return False
    report = run.report if isinstance(run.report, dict) else {}
    if report.get("fatal"):
        return False
    return (now or timezone.now()) - run.finished_at <= DRY_RUN_MAX_AGE


def apply_deadline(run: PancakePosSyncRun | None) -> datetime | None:
    """Hạn cuối được ghi theo lượt dry-run này (None = không ghi được)."""
    if run is None or run.finished_at is None or not dry_run_ok(run):
        return None
    return run.finished_at + DRY_RUN_MAX_AGE


def scope_of(run: PancakePosSyncRun) -> dict[str, Any]:
    report = run.report if isinstance(run.report, dict) else {}
    scope = report.get("scope") if isinstance(report.get("scope"), dict) else {}
    return {"warehouse_ids": scope.get("warehouse_ids") or None, "skus": scope.get("skus") or None}


def check_apply_allowed(dry_run_id: int) -> dict[str, Any]:
    """Lượt dry-run `dry_run_id` phải là dry-run gần nhất, thành công, ≤ 30 phút.
    Trả phạm vi của nó; không đạt → DryRunRequired."""
    latest = latest_dry_run()
    if latest is None or latest.id != dry_run_id:
        raise DryRunRequired("Cần ghi theo lượt chạy thử GẦN NHẤT — hãy chạy thử lại.")
    if not dry_run_ok(latest):
        raise DryRunRequired("Lượt chạy thử lỗi hoặc đã quá 30 phút — hãy chạy thử lại.")
    return scope_of(latest)


def create_run(*, dry_run: bool, user_id: int | None) -> int:
    mode = SyncRunMode.INITIAL_STOCK_DRY_RUN if dry_run else SyncRunMode.INITIAL_STOCK
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [_ADVISORY_KEY])
        if active_run() is not None:
            raise InitialStockAlreadyRunning("Đang có lượt tồn đầu kỳ khác chưa xong")
        return LinkRepository.start_run(mode, user_id)


def execute_run(
    run_id: int,
    *,
    dry_run: bool,
    warehouse_ids: Sequence[int] | None = None,
    skus: Sequence[str] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Chạy lượt đã tạo sẵn. `{"reason": "locked"}` = chưa giành được khoá."""
    with single_flight(LOCK_KEY, LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            return {"reason": "locked"}
        try:
            service = build_initial_stock_service(dry_run=dry_run)
        except PancakePosError as exc:
            fail_run(run_id, str(exc))
            return {"reason": "error", "detail": str(exc)}
        try:
            report = service.run(
                dry_run=dry_run, warehouse_ids=warehouse_ids, skus=skus, user_id=user_id, run_id=run_id
            )
        except PancakePosError as exc:
            # Service đã đóng nhật ký với `fatal`; không raise để Celery không retry.
            return {"reason": "error", "detail": str(exc)}
        return {"run_id": report.run_id, **report.totals}
