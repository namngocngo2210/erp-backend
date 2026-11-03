"""Facade module to preserve public imports while delegating to layered implementation.

Celery autodiscovery import module này — import dưới đây nạp toàn bộ task con
(side effect đăng ký task), không chỉ re-export tên.
"""

from .infrastructure.tasks.order_tasks_impl import (  # noqa: F401
    NO_ANSWER_CODES,
    close_converted_leads,
    logger,
    notify_lark_cancelled_after_dispatch,
    notify_lark_order_created,
    notify_lark_vat_invoice_required,
    process_auto_sync_invoice_full,
    process_kiotviet_job,
    process_landing_ingest,
    process_no_answer_reminders,
    process_restore_orders,
    process_sale_reminders,
    purge_old_ingest_traces,
    purge_old_journey_events,
    sweep_floating_pool,
    sweep_lead_assignment,
)
