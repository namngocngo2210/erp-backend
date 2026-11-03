"""Single wrapper for every ``.delay() / .apply_async()`` call site.

All async work runs on Celery. ``dispatch()`` maps a short task name (e.g.
``kv.process_job``) to its Celery task via the registry and enqueues it on the
given queue. ``write_audit()`` buffers audit-log rows and enqueues them in
batches.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from .registry import get_celery_task

logger = logging.getLogger(__name__)

# ── Buffer batch audit (sự cố 2026-08-03/04) ──
# Mỗi request một task ``audit.create`` = ~260 task churn/phút trên queue audit
# giữa burst tạo đơn buổi sáng. Buffer per-process (mỗi gunicorn worker một
# buffer riêng): đầy AUDIT_BATCH_SIZE thì enqueue MỘT task ``audit.create_batch``;
# batch lửng được ``threading.Timer`` flush sau AUDIT_BATCH_MAX_DELAY_SECONDS để
# lúc vắng traffic audit không nằm chờ vô hạn trong RAM. Đánh đổi chấp nhận:
# process dừng (deploy/OOM) trước khi timer kịp chạy thì mất tối đa MỘT batch
# chưa flush (mặc định ≤20 dòng / ≤2s traffic) — audit là log đối soát, không
# phải sổ cái; đổi lấy việc cắt task churn ~10-20 lần lúc burst.
_audit_buffer: list[dict[str, Any]] = []
_audit_lock = threading.Lock()
_audit_timer: threading.Timer | None = None


def dispatch(
    task_name: str,
    *,
    queue: str = "default",
    countdown: int = 0,
    priority: int = 0,  # kept for call-site compatibility; Celery has no per-call priority here
    max_attempts: int = 5,  # kept for call-site compatibility
    **payload: Any,
):
    """Drop-in for ``xxx.delay(**kwargs)`` / ``xxx.apply_async(...)``.

    ``task_name`` is the short dotted name (e.g. ``kv.process_job``) — see registry.py.
    Returns a Celery ``AsyncResult`` (callers may read ``.id``).
    """
    celery_task = get_celery_task(task_name)
    if countdown:
        return celery_task.apply_async(kwargs=payload, queue=queue, countdown=countdown)
    return celery_task.apply_async(kwargs=payload, queue=queue)


def _audit_batch_settings() -> tuple[int, float]:
    from django.conf import settings

    size = int(getattr(settings, "AUDIT_BATCH_SIZE", 20))
    delay = float(getattr(settings, "AUDIT_BATCH_MAX_DELAY_SECONDS", 2.0))
    return size, delay


def write_audit(**payload: Any) -> None:
    """Buffer one audit-log row; enqueue as a batch via Celery when due.

    Flush khi buffer đạt AUDIT_BATCH_SIZE, hoặc timer nổ sau
    AUDIT_BATCH_MAX_DELAY_SECONDS kể từ entry đầu của batch. Không bao giờ raise —
    audit hỏng không được làm hỏng request.
    """
    global _audit_timer
    size, delay = _audit_batch_settings()
    batch: list[dict[str, Any]] | None = None
    with _audit_lock:
        _audit_buffer.append(payload)
        if len(_audit_buffer) >= size:
            batch = _audit_buffer[:]
            _audit_buffer.clear()
            if _audit_timer is not None:
                _audit_timer.cancel()
                _audit_timer = None
        elif _audit_timer is None:
            _audit_timer = threading.Timer(delay, _flush_audit_buffer)
            _audit_timer.daemon = True
            _audit_timer.start()
    if batch:
        _enqueue_audit_batch(batch)


def _flush_audit_buffer() -> None:
    """Đổ hết buffer hiện tại vào một task batch (chạy từ timer hoặc test)."""
    global _audit_timer
    with _audit_lock:
        batch = _audit_buffer[:]
        _audit_buffer.clear()
        if _audit_timer is not None:
            _audit_timer.cancel()
            _audit_timer = None
    if batch:
        _enqueue_audit_batch(batch)


def _enqueue_audit_batch(entries: list[dict[str, Any]]) -> None:
    try:
        get_celery_task("audit.create_batch").delay(entries=entries)
        return
    except Exception as exc:  # noqa: BLE001
        logger.error("audit batch enqueue failed, writing sync: %s", exc)
    try:
        from apps.audit.models import AuditLog

        AuditLog.objects.bulk_create([AuditLog(**entry) for entry in entries])
    except Exception as exc2:  # noqa: BLE001
        logger.error("audit sync DB fallback failed: %s", exc2)


def _reset_audit_buffer_for_tests() -> None:
    """Xoá sạch buffer + timer giữa các test — không dùng trong production."""
    global _audit_timer
    with _audit_lock:
        _audit_buffer.clear()
        if _audit_timer is not None:
            _audit_timer.cancel()
            _audit_timer = None
