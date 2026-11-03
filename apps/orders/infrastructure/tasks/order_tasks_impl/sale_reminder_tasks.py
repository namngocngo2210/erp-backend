"""Tasks that drive sale-status Lark reminders.

* `process_sale_reminders`        — every minute, pull due `SaleReminder` rows.
* `process_no_answer_reminders`   — fires from 4 fixed cron slots a day
                                    (08:00 / 11:00 / 15:00 / 16:30 Asia/Bangkok).
                                    Each invocation passes a `slot` kwarg so
                                    we pick the right message template. The
                                    body only shows the aggregate count per
                                    sale, not the per-order list.
"""
from __future__ import annotations

import logging

from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.common.db_guards import single_flight

from apps.orders.infrastructure.lark_notifier import (
    send_sale_reminder_post,
    send_sale_reminder_text,
)
from apps.orders.infrastructure.sale_reminder_templates import (
    NO_ANSWER_SLOTS,
    build_no_answer_summary_post,
    build_reminder_message,
)
from apps.orders.models import (
    OrderAssessment,
    OrderSource,
    SaleReminder,
    SaleReminderStatus,
)

logger = logging.getLogger(__name__)


NO_ANSWER_CODES = (
    "no_answer_1",
    "no_answer_2",
    "no_answer_3",
    "no_answer_4",
    "no_answer_5",
    "no_answer_6",
)


# ----- process_sale_reminders ----------------------------------------------


@shared_task(
    bind=True,
    ignore_result=True,
    soft_time_limit=60,
    time_limit=90,
    name="apps.orders.tasks.process_sale_reminders",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
)
def process_sale_reminders(self, batch_size: int = 100):
    """Send any pending `SaleReminder` whose `due_at <= now`.

    Idempotent: a reminder is marked SENT only after Lark accepts it. On
    transient failure we push `due_at` forward (backoff) so the next tick
    retries WITHOUT the failed row pinning the head of the queue.
    """
    # time_limit (90s) > chu kỳ beat (60s) → 2 tick chồng nhau cùng đọc một
    # batch PENDING và Lark nhận TRÙNG tin nhắn (guard status chỉ chặn update,
    # message đã bắn). Single-flight: tick sau skip khi tick trước còn chạy
    # (audit 2026-07-14 vòng 5).
    with single_flight("lock:process_sale_reminders", 120) as acquired:
        if not acquired:
            logger.warning("process_sale_reminders already running — skip this tick")
            return {"sent": -1, "failed": 0}

        now = timezone.now()
        qs = (
            SaleReminder.objects.select_related("order", "staff")
            .filter(status=SaleReminderStatus.PENDING, due_at__lte=now)
            .order_by("due_at")[:batch_size]
        )
        rows = list(qs)
        if not rows:
            return {"sent": 0, "failed": 0}

        sent = 0
        failed = 0
        for reminder in rows:
            try:
                text = build_reminder_message(reminder)
            except Exception:
                logger.exception("Failed to render reminder text id=%s", reminder.id)
                SaleReminder.objects.filter(pk=reminder.pk).update(
                    status=SaleReminderStatus.FAILED,
                    last_error="render_failed",
                )
                failed += 1
                continue

            ok = send_sale_reminder_text(text)
            if ok:
                SaleReminder.objects.filter(pk=reminder.pk, status=SaleReminderStatus.PENDING).update(
                    status=SaleReminderStatus.SENT,
                    sent_at=timezone.now(),
                    last_error="",
                )
                sent += 1
            else:
                # Retry với BACKOFF thay vì giữ nguyên due_at: row hỏng bền vững
                # (staff mất lark id, template lỗi phía Lark) mà cứ nằm PENDING với
                # due_at cũ sẽ chiếm đầu hàng `order_by(due_at)[:batch]` VĨNH VIỄN —
                # đủ `batch_size` row như vậy thì reminder mới không bao giờ được
                # gửi (audit 2026-07-14 vòng 5). Đẩy về sau 10' là nhường chỗ.
                SaleReminder.objects.filter(pk=reminder.pk).update(
                    last_error="lark_post_failed",
                    due_at=timezone.now() + timedelta(minutes=10),
                )
                failed += 1

        if sent or failed:
            logger.info("process_sale_reminders sent=%d failed=%d", sent, failed)
        return {"sent": sent, "failed": failed}


# ----- process_no_answer_reminders -----------------------------------------


@shared_task(
    bind=True,
    ignore_result=True,
    soft_time_limit=60,
    time_limit=90,
    name="apps.orders.tasks.process_no_answer_reminders",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
)
def process_no_answer_reminders(self, slot: str):
    """One KNM nag per sale, sent at one of the 4 fixed shift slots.

    `slot` must be one of `NO_ANSWER_SLOTS`. Per spec we only output the
    aggregate count (bold) — no per-order list. Only landing-source orders
    are counted.
    """
    if slot not in NO_ANSWER_SLOTS:
        logger.warning("process_no_answer_reminders: invalid slot=%r", slot)
        return {"skipped": "invalid_slot"}

    rows = (
        OrderAssessment.objects.filter(
            sales_status_code__in=NO_ANSWER_CODES,
            order__staff__isnull=False,
            order__order_source=OrderSource.LANDING,
        )
        .values("order__staff_id")
        .annotate(count=Count("id"))
    )
    counts_by_staff: dict[int, int] = {row["order__staff_id"]: row["count"] for row in rows}
    if not counts_by_staff:
        return {"staff_notified": 0}

    # Load matching staff in one shot so we can render their display name /
    # lark_open_id without a query per row.
    from apps.users.models import User  # local import to avoid app-init cycles

    staff_by_id = {
        u.id: u for u in User.objects.filter(id__in=list(counts_by_staff.keys()))
    }

    sent_count = 0
    failed_count = 0
    for staff_id, count in counts_by_staff.items():
        staff = staff_by_id.get(staff_id)
        if staff is None:
            continue
        body = build_no_answer_summary_post(staff=staff, count=count, slot=slot)
        if send_sale_reminder_post(body):
            with transaction.atomic():
                User.objects.filter(pk=staff_id).update(
                    last_no_answer_reminder_at=timezone.now()
                )
            sent_count += 1
        else:
            failed_count += 1

    if sent_count or failed_count:
        logger.info(
            "process_no_answer_reminders slot=%s sent=%d failed=%d",
            slot,
            sent_count,
            failed_count,
        )
    return {"slot": slot, "staff_notified": sent_count, "failed": failed_count}
