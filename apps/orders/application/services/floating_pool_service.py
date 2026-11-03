"""Kho số thả nổi: sweep (thu hồi đơn bỏ bê vào kho) + claim (sale nhặt số).

Spec: docs/sale-floating-pool.md. Pattern transaction/lock mirror
LeadAssignmentService (lead_assignment_service.py).
"""

import logging
from datetime import timedelta

from django.db.models import Count, F, Q
from django.db import transaction
from django.utils import timezone

from apps.orders.models import (
    FloatingPoolConfig,
    FloatingPoolEntry,
    Order,
    OrderStatus,
)
# Hóa đơn KiotViet đã hủy (xem import-cancelled-invoices) — đơn coi như chết.
from apps.orders.order_status_constants import CANCELLED_INVOICE_STATUS

logger = logging.getLogger(__name__)

# Trần số dòng mỗi lượt sweep xử lý (cả chiều gỡ lẫn chiều đưa vào kho): backlog
# đột biến (vd đổi config stale_days khiến hàng chục nghìn đơn đủ điều kiện cùng
# lúc) không được kéo dài 1 tick cron vô hạn. Ưu tiên dòng CŨ nhất; phần dư tick
# 15' sau vớt tiếp — sweep vốn idempotent (audit 2026-07-28 DB-14).
SWEEP_MAX_ROWS_PER_TICK = 2000


class ClaimError(Exception):
    status_code = 400


class ClaimConflict(ClaimError):
    status_code = 409


class ClaimForbidden(ClaimError):
    status_code = 403


def mask_phone(phone: str) -> str:
    """Che giữa SĐT: 0935112234 -> 0935***234; chuỗi quá ngắn che hết."""
    digits = str(phone or "").strip()
    if len(digits) < 9:
        return "***"
    return f"{digits[:4]}***{digits[-3:]}"


class FloatingPoolService:
    @staticmethod
    def get_config() -> FloatingPoolConfig:
        return FloatingPoolConfig.get_solo()

    @staticmethod
    def sweep(now=None) -> dict:
        """Cron 15': gỡ entry đã được cứu + đưa đơn bỏ bê vào kho. Idempotent."""
        config = FloatingPoolService.get_config()
        if not config.enabled:
            return {"skipped": "disabled"}
        now = now or timezone.now()
        cutoff = now - timedelta(days=config.stale_days)

        removed = FloatingPoolService._remove_rescued(config, now)

        base = (
            Order.objects.filter(staff__isnull=False, deleted_at__isnull=True)
            .exclude(status=OrderStatus.CANCELLED)
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            # Đơn đã lên hóa đơn KiotViet = đã chốt thật, mặc nhiên được cứu (spec §3.2).
            .exclude(invoice_code__gt="")
            .exclude(floating_entries__status=FloatingPoolEntry.Status.FLOATING)
            # Nhặt số = tác nghiệp: pick gần đây reset đồng hồ X ngày.
            # Subquery để 2 điều kiện bind cùng 1 row — exclude() đa điều kiện
            # trên quan hệ nhiều dòng không đảm bảo same-row (Django gotcha).
            .exclude(
                id__in=FloatingPoolEntry.objects.filter(
                    status=FloatingPoolEntry.Status.PICKED, picked_at__gt=cutoff
                ).values("order_id")
            )
            .filter(
                Q(assessment__isnull=True, created_at__lte=cutoff)
                | Q(assessment__updated_at__lte=cutoff)
            )
            .filter(
                Q(assessment__isnull=True)
                | Q(assessment__next_followup_at__isnull=True)
                | Q(assessment__next_followup_at__lt=now)
            )
        )
        if config.saved_status_codes:
            base = base.exclude(assessment__sales_status_code__in=config.saved_status_codes)
        if config.max_order_age_days > 0:
            base = base.filter(created_at__gte=now - timedelta(days=config.max_order_age_days))
        base = base.annotate(
            rounds_used=Count(
                "floating_entries",
                filter=Q(floating_entries__status=FloatingPoolEntry.Status.PICKED),
            )
        ).filter(rounds_used__lt=config.max_float_count)

        # Cũ nhất trước (order_by created_at) + trần mỗi tick — xem SWEEP_MAX_ROWS_PER_TICK.
        candidates = list(
            base.order_by("created_at", "id")
            # rounds_used đã annotate sẵn = số entry PICKED — tránh N+1.
            .values("id", "staff_id", "rounds_used")[:SWEEP_MAX_ROWS_PER_TICK]
        )
        # bulk_create(ignore_conflicts) thay cho get_or_create từng dòng (2 round-trip
        # × N đơn): ON CONFLICT DO NOTHING không khai conflict-target nên khớp luôn
        # partial unique `floating_entry_one_active_per_order` (order, status=floating)
        # — race với sweep/claim song song chỉ làm dòng đó bị bỏ qua, đúng ngữ nghĩa
        # get_or_create cũ (audit 2026-07-28 DB-14).
        FloatingPoolEntry.objects.bulk_create(
            [
                FloatingPoolEntry(
                    order_id=row["id"],
                    status=FloatingPoolEntry.Status.FLOATING,
                    previous_staff_id=row["staff_id"],
                    entered_at=now,
                    float_count=row["rounds_used"] + 1,
                )
                for row in candidates
            ],
            ignore_conflicts=True,
        )
        # ignore_conflicts không cho biết số dòng THẬT SỰ chèn — đếm lại theo
        # entered_at=now (chỉ khớp entry của đúng lượt sweep này).
        created = (
            FloatingPoolEntry.objects.filter(
                order_id__in=[row["id"] for row in candidates],
                status=FloatingPoolEntry.Status.FLOATING,
                entered_at=now,
            ).count()
            if candidates
            else 0
        )
        logger.info("floating-pool sweep: created=%s removed=%s", created, removed)
        return {"created": created, "removed": removed}

    @staticmethod
    def _remove_rescued(config, now) -> int:
        """Gỡ entry đã được cứu bằng 2 câu UPDATE hàng loạt thay cho vòng lặp
        SELECT + save() từng entry (audit 2026-07-28 DB-14). ``updated_at`` phải
        set tay: ``.update()`` không chạy ``auto_now``."""
        # Trần mỗi tick, cũ nhất trước — xem SWEEP_MAX_ROWS_PER_TICK.
        floating_ids = list(
            FloatingPoolEntry.objects.filter(status=FloatingPoolEntry.Status.FLOATING)
            .order_by("entered_at", "id")
            .values_list("id", flat=True)[:SWEEP_MAX_ROWS_PER_TICK]
        )
        if not floating_ids:
            return 0
        # Điều kiện status=FLOATING lặp lại để CANCELLED thắng khi 1 đơn khớp cả 2
        # nhóm (giữ ưu tiên if/elif cũ): dòng đã flip ở câu UPDATE trước tự bị loại.
        base = FloatingPoolEntry.objects.filter(
            id__in=floating_ids, status=FloatingPoolEntry.Status.FLOATING
        )
        cancelled_q = (
            Q(order__deleted_at__isnull=False)
            | Q(order__status=OrderStatus.CANCELLED)
            | Q(order__invoice_status=CANCELLED_INVOICE_STATUS)
        )
        removed = base.filter(cancelled_q).update(
            status=FloatingPoolEntry.Status.REMOVED,
            removed_reason=FloatingPoolEntry.RemovedReason.CANCELLED,
            updated_at=now,
        )
        saved_q = (
            # Đơn đã lên hóa đơn KiotViet = đã chốt thật, mặc nhiên được cứu (spec §3.2).
            Q(order__invoice_code__gt="")
            # Đơn đã đổi sale (transfer) sau khi vào kho → gỡ, sale mới không có lỗi
            # bỏ bê. Viết đủ 3 nhánh vì SQL `a <> b` với NULL là UNKNOWN (bị loại),
            # còn logic Python cũ so None != id là True — thiếu nhánh isnull sẽ bỏ
            # sót ca đơn bị gỡ staff hoặc previous_staff đã bị xoá user.
            | Q(order__staff_id__isnull=True, previous_staff_id__isnull=False)
            | Q(order__staff_id__isnull=False, previous_staff_id__isnull=True)
            | ~Q(order__staff_id=F("previous_staff_id"))
            | Q(order__assessment__next_followup_at__gt=now)
            | Q(order__assessment__updated_at__gt=F("entered_at"))
        )
        if config.saved_status_codes:
            saved_q |= Q(
                order__assessment__sales_status_code__in=config.saved_status_codes
            )
        removed += base.filter(saved_q).update(
            status=FloatingPoolEntry.Status.REMOVED,
            removed_reason=FloatingPoolEntry.RemovedReason.SAVED,
            updated_at=now,
        )
        return removed

    @staticmethod
    def claimed_today(user) -> int:
        today = timezone.localdate()
        return FloatingPoolEntry.objects.filter(
            picked_by=user, status=FloatingPoolEntry.Status.PICKED, picked_at__date=today
        ).count()

    @staticmethod
    def claim(entry_id: int, user):
        """Nhặt số: lock entry chống race (mirror LeadAssignmentService), đổi staff.

        Nhánh "đơn hết hiệu lực" phải commit việc gỡ entry (REMOVED) trước khi báo
        lỗi cho caller: raise ngay trong ``with transaction.atomic()`` sẽ rollback
        luôn save() vừa làm (Django rollback toàn bộ atomic block khi có exception
        thoát ra ngoài) — nên đánh dấu conflict rồi raise SAU khi khối atomic đã
        đóng thành công.
        """
        config = FloatingPoolService.get_config()
        conflict_message = None
        with transaction.atomic():
            entry = (
                FloatingPoolEntry.objects.select_for_update().get(pk=entry_id)
            )
            if entry.status != FloatingPoolEntry.Status.FLOATING:
                raise ClaimConflict("Số đã có người nhặt.")
            if entry.previous_staff_id == user.id:
                raise ClaimForbidden("Bạn không thể nhặt lại số mình vừa bị thu hồi.")
            if config.daily_claim_limit > 0 and (
                FloatingPoolService.claimed_today(user) >= config.daily_claim_limit
            ):
                raise ClaimError("Đã dùng hết lượt nhặt hôm nay.")
            order = Order.objects.select_for_update().get(pk=entry.order_id)
            if (
                order.deleted_at is not None
                or order.status == OrderStatus.CANCELLED
                or order.invoice_status == CANCELLED_INVOICE_STATUS
            ):
                entry.status = FloatingPoolEntry.Status.REMOVED
                entry.removed_reason = FloatingPoolEntry.RemovedReason.CANCELLED
                entry.save(update_fields=["status", "removed_reason", "updated_at"])
                conflict_message = "Đơn không còn hiệu lực."
            elif order.invoice_code:
                # Đơn đã lên hóa đơn KiotViet giữa lúc đang nổi = đã chốt thật, gỡ khỏi kho.
                entry.status = FloatingPoolEntry.Status.REMOVED
                entry.removed_reason = FloatingPoolEntry.RemovedReason.SAVED
                entry.save(update_fields=["status", "removed_reason", "updated_at"])
                conflict_message = "Đơn đã lên hóa đơn."
            elif order.staff_id != entry.previous_staff_id:
                # Đơn đã đổi sale (transfer) sau khi vào kho → gỡ, sale mới không có lỗi bỏ bê.
                entry.status = FloatingPoolEntry.Status.REMOVED
                entry.removed_reason = FloatingPoolEntry.RemovedReason.SAVED
                entry.save(update_fields=["status", "removed_reason", "updated_at"])
                conflict_message = "Đơn đã được chuyển cho sale khác."
            else:
                order.staff = user
                order.save(update_fields=["staff", "updated_at"])
                entry.status = FloatingPoolEntry.Status.PICKED
                entry.picked_by = user
                entry.picked_at = timezone.now()
                entry.save(update_fields=["status", "picked_by", "picked_at", "updated_at"])
        if conflict_message:
            raise ClaimConflict(conflict_message)
        logger.info(
            "floating-pool claim: entry=%s order=%s user=%s", entry.id, order.id, user.id
        )
        return order
