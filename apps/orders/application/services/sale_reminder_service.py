"""Schedule / cancel `SaleReminder` rows when `OrderAssessment.sales_status_code` changes.

Each sales_status_code triggers a different reminder strategy:

| code                    | strategy                                                |
|-------------------------|---------------------------------------------------------|
| need_more_consulting    | 3 one-shot reminders at D+2, D+4, D+7 @ 09:00 local     |
| scheduled               | 1 one-shot reminder at user-picked `next_followup_at`   |
| call_back_later         | 1 one-shot reminder at user-picked `next_followup_at`   |
| closed_scheduled_date   | 1 one-shot reminder at user-picked `next_followup_at`   |
| closed_express          | 1 reminder fired immediately (due_at = now)             |

When the code changes to anything outside this set, all pending reminders for
the order are cancelled with reason="status_changed".
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from apps.orders.models import (
    Order,
    OrderAssessment,
    OrderSource,
    SaleReminder,
    SaleReminderKind,
    SaleReminderStatus,
)
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)


NEED_MORE_CONSULTING_CODE = "need_more_consulting"
SCHEDULED_CODE = "scheduled"
CALL_BACK_LATER_CODE = "call_back_later"
CLOSED_SCHEDULED_DATE_CODE = "closed_scheduled_date"
CLOSED_EXPRESS_CODE = "closed_express"

ONE_SHOT_WITH_USER_DATE_CODES: set[str] = {
    SCHEDULED_CODE,
    CALL_BACK_LATER_CODE,
    CLOSED_SCHEDULED_DATE_CODE,
}

CODE_TO_KIND: dict[str, SaleReminderKind] = {
    SCHEDULED_CODE: SaleReminderKind.SCHEDULED,
    CALL_BACK_LATER_CODE: SaleReminderKind.CALL_BACK_LATER,
    CLOSED_SCHEDULED_DATE_CODE: SaleReminderKind.CLOSED_SCHEDULED_DATE,
    CLOSED_EXPRESS_CODE: SaleReminderKind.CLOSED_EXPRESS,
}


def _local_now() -> datetime:
    return timezone.localtime(timezone.now())


def _at_local_hour(base_date, hour: int) -> datetime:
    """Build an aware datetime at `hour:00:00` local on the given date."""
    naive = datetime.combine(base_date, time(hour=hour, minute=0))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _need_more_consulting_due_dates(now_local: datetime) -> list[tuple[SaleReminderKind, datetime]]:
    follow_hour = int(sysconfig.get("SALE_REMINDER_FOLLOWUP_HOUR"))
    base = now_local.date()
    return [
        (SaleReminderKind.NEED_MORE_CONSULTING_D2, _at_local_hour(base + timedelta(days=2), follow_hour)),
        (SaleReminderKind.NEED_MORE_CONSULTING_D4, _at_local_hour(base + timedelta(days=4), follow_hour)),
        (SaleReminderKind.NEED_MORE_CONSULTING_D7, _at_local_hour(base + timedelta(days=7), follow_hour)),
    ]


def _cancel_pending_reminders(order: Order, *, reason: str) -> int:
    now = timezone.now()
    updated = SaleReminder.objects.filter(
        order=order, status=SaleReminderStatus.PENDING
    ).update(status=SaleReminderStatus.CANCELLED, cancelled_at=now, cancel_reason=reason[:120])
    if updated:
        logger.info("Cancelled %d pending reminders for order=%s (reason=%s)", updated, order.id, reason)
    return updated


def _create_reminders(
    *,
    order: Order,
    assessment: OrderAssessment,
    actor,
    entries: Iterable[tuple[SaleReminderKind, datetime]],
) -> list[SaleReminder]:
    note = (assessment.internal_note or "").strip()
    staff = order.staff or actor
    sales_code = assessment.sales_status_code or ""
    rows = [
        SaleReminder(
            order=order,
            staff=staff,
            kind=kind,
            due_at=due_at,
            status=SaleReminderStatus.PENDING,
            sales_status_code_snapshot=sales_code,
            note_snapshot=note,
        )
        for kind, due_at in entries
    ]
    if not rows:
        return []
    SaleReminder.objects.bulk_create(rows)
    return rows


@transaction.atomic
def reschedule_for_assessment(
    *,
    order: Order,
    assessment: OrderAssessment,
    actor,
    new_sales_status_code: str | None,
    next_followup_at: datetime | None,
) -> list[SaleReminder]:
    """Cancel current pending reminders and (re)create them based on the new status.

    Called from `_upsert_order_assessment` only when `sales_status_code` or
    `next_followup_at` actually changes. Returns the freshly created rows.

    Skipped entirely for non-landing orders — Lark reminders only target the
    landing-page sale flow per requirements.
    """
    code = (new_sales_status_code or "").strip()

    if order.order_source != OrderSource.LANDING:
        # Still cancel any leftovers from when the order was on the landing
        # flow, then bail.
        _cancel_pending_reminders(order, reason="non_landing_source")
        return []

    _cancel_pending_reminders(order, reason=f"status→{code or 'empty'}")

    if code == NEED_MORE_CONSULTING_CODE:
        entries = _need_more_consulting_due_dates(_local_now())
        return _create_reminders(order=order, assessment=assessment, actor=actor, entries=entries)

    if code in ONE_SHOT_WITH_USER_DATE_CODES:
        if next_followup_at is None:
            logger.info(
                "Skip scheduling reminder for order=%s code=%s — next_followup_at is required",
                order.id,
                code,
            )
            return []
        kind = CODE_TO_KIND[code]
        return _create_reminders(
            order=order,
            assessment=assessment,
            actor=actor,
            entries=[(kind, next_followup_at)],
        )

    if code == CLOSED_EXPRESS_CODE:
        return _create_reminders(
            order=order,
            assessment=assessment,
            actor=actor,
            entries=[(SaleReminderKind.CLOSED_EXPRESS, timezone.now())],
        )

    return []


@transaction.atomic
def schedule_resale_appointment_reminder(
    *,
    order: Order,
    appointment_at: datetime | None,
    actor=None,
    note: str = "",
) -> SaleReminder | None:
    """(Re)schedule the single Lark reminder for a resale appointment.

    Called from `CustomerService.update_resale_appointment` whenever a sale sets,
    changes, or clears the appointment time on `CustomerResaleFollowUp`. Unlike
    the sale-flow reminders this is NOT gated by `OrderSource.LANDING` — resale
    tracking runs on every delivered-success order regardless of source.

    Always cancels any pending `RESALE_APPOINTMENT` reminder for the order first
    (so at most one stays PENDING), then creates a fresh one due at
    `appointment_at`. A missing or past `appointment_at` only cancels — there is
    nothing useful to remind about. Other reminder kinds on the order are left
    untouched.
    """
    SaleReminder.objects.filter(
        order=order,
        kind=SaleReminderKind.RESALE_APPOINTMENT,
        status=SaleReminderStatus.PENDING,
    ).update(
        status=SaleReminderStatus.CANCELLED,
        cancelled_at=timezone.now(),
        cancel_reason="resale_appointment_changed",
    )

    if appointment_at is None or appointment_at <= timezone.now():
        logger.info(
            "Skip resale appointment reminder for order=%s — appointment_at=%s is empty or past",
            order.id,
            appointment_at,
        )
        return None

    reminder = SaleReminder.objects.create(
        order=order,
        staff=order.staff or actor,
        kind=SaleReminderKind.RESALE_APPOINTMENT,
        due_at=appointment_at,
        status=SaleReminderStatus.PENDING,
        note_snapshot=(note or "").strip(),
    )
    return reminder


def cancel_all_for_order(order: Order, *, reason: str = "manual") -> int:
    return _cancel_pending_reminders(order, reason=reason)


def get_next_reminder_at(order_id: int) -> datetime | None:
    row = (
        SaleReminder.objects.filter(order_id=order_id, status=SaleReminderStatus.PENDING)
        .order_by("due_at")
        .values_list("due_at", flat=True)
        .first()
    )
    return row


def get_next_reminder_at_map(order_ids: Iterable[int]) -> dict[int, datetime]:
    """Batched variant of `get_next_reminder_at` for list endpoints.

    Returns {order_id: earliest pending due_at}. Avoids 1 query per row when
    serializing a page of orders.
    """
    ids = [oid for oid in order_ids if oid]
    if not ids:
        return {}
    from django.db.models import Min

    rows = (
        SaleReminder.objects.filter(order_id__in=ids, status=SaleReminderStatus.PENDING)
        .values("order_id")
        .annotate(due=Min("due_at"))
    )
    return {row["order_id"]: row["due"] for row in rows}
