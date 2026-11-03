"""Mapping ``dispatch()`` short task name -> Celery task object.

Lazy import to avoid circular imports during Django startup. Every key is a short
dotted name (e.g. ``kv.process_job``) shared with the old Go task-worker handlers.
"""
from __future__ import annotations

from importlib import import_module
from collections.abc import Callable


def _lazy(module_path: str, attr: str) -> Callable[[], object]:
    def _resolve() -> object:
        mod = import_module(module_path)
        return getattr(mod, attr)

    return _resolve


_REGISTRY: dict[str, Callable[[], object]] = {
    # ── Audit ──
    # "audit.create" giữ lại cho task còn nằm trong queue lúc rolling deploy;
    # đường chính từ 2026-08-04 là batch (write_audit gom entry — xem api.py).
    "audit.create": _lazy("apps.audit.tasks", "create_audit_log_task"),
    "audit.create_batch": _lazy("apps.audit.tasks", "create_audit_log_batch_task"),
    # ── KiotViet ──
    "kv.process_job": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks",
        "process_kiotviet_job",
    ),
    "kv.revenue_sync": _lazy(
        "apps.kiotviet.tasks", "sync_daily_revenue_all_channels"
    ),
    # ── Facebook Ads ──
    "fb.daily_spend": _lazy("apps.facebook_ads.tasks", "sync_all_daily_spend"),
    "ads.report_lark_sync": _lazy("apps.facebook_ads.tasks", "sync_ads_report_lark"),
    # ── Lark ──
    "lark.notify_order_created": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.lark_notification_tasks",
        "notify_lark_order_created",
    ),
    "lark.notify_cancelled_after_dispatch": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.lark_notification_tasks",
        "notify_lark_cancelled_after_dispatch",
    ),
    "lark.notify_vat_invoice_required": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.lark_notification_tasks",
        "notify_lark_vat_invoice_required",
    ),
    # ── Foxia ──
    "foxia.push_order": _lazy("apps.foxia.tasks", "push_order_to_foxia"),
    # ── Orders (formerly Pattern B) ──
    "orders.sale_reminders": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.sale_reminder_tasks",
        "process_sale_reminders",
    ),
    "orders.no_answer_reminders": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.sale_reminder_tasks",
        "process_no_answer_reminders",
    ),
    "orders.restore_orders": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.restore_tasks",
        "process_restore_orders",
    ),
    "orders.landing_ingest": _lazy(
        "apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks",
        "process_landing_ingest",
    ),
}


def get_celery_task(task_name: str):
    if task_name not in _REGISTRY:
        raise KeyError(f"Unknown dispatch task name: {task_name}")
    return _REGISTRY[task_name]()


def all_task_names() -> list[str]:
    return sorted(_REGISTRY.keys())
