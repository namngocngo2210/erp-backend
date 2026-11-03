"""Hàng đợi lệnh KiotViet theo đơn (``OrderKiotVietJob``): dọn job treo, tạo job, đẩy queue.

Ba endpoint (cancel-kiotviet, kiotviet-actions, kship-delivery) từng lặp nguyên một
khối: quét job PENDING/RUNNING quá hạn → đánh FAILED, loại đơn đang có job sống,
``bulk_create`` lô job mới, ``dispatch`` từng job có giãn cách, đánh FAILED job không
đẩy được, rồi phát event ``order.updated`` cho cả lô. Gom về đây để view chỉ còn
parse/validate → gọi service → serialize (audit 2026-09-23 T3/P2-1); guard
``apps/common/tests/test_no_atomic_in_transport.py`` chặn ``bulk_create`` quay lại
tầng transport.

KHÔNG bọc ``transaction.atomic`` quanh cả ``enqueue``: job phải COMMIT trước khi
``dispatch`` để worker nhận ``job_id`` là thấy ngay hàng; ``bulk_create`` tự nguyên
tử trong một INSERT là đủ.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.dispatch import dispatch
from apps.orders.application.services.order_event_publisher import load_event_meta
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.contracts import OrderEventContract
from apps.orders.models import OrderKiotVietJob, OrderKiotVietJobStatus

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = (OrderKiotVietJobStatus.PENDING, OrderKiotVietJobStatus.RUNNING)
_STALE_JOB_MESSAGE = "Lệnh KiotViet trước quá hạn, đang thử lại."
_DISPATCH_FAILED_MESSAGE = "Không gửi được lệnh tới hàng đợi KiotViet."


@dataclass(frozen=True)
class KiotVietJobEnqueueResult:
    """Kết quả một lượt ``enqueue``: job đã tạo + job không đẩy được vào queue."""

    created_jobs: list[OrderKiotVietJob]
    dispatch_failed_job_ids: list[int]

    @property
    def created_count(self) -> int:
        return len(self.created_jobs)

    @property
    def dispatch_failed_count(self) -> int:
        return len(self.dispatch_failed_job_ids)

    @property
    def queued_count(self) -> int:
        """Số job thực sự nằm trong queue (đã tạo trừ đi số dispatch hỏng)."""
        return max(0, self.created_count - self.dispatch_failed_count)


class OrderKiotVietJobService:
    """Vòng đời "tạo + đẩy" job KiotViet cho một lô đơn."""

    @staticmethod
    def _active_cutoff():
        timeout_seconds = int(getattr(settings, "KIOTVIET_JOB_ACTIVE_TIMEOUT_SECONDS", 300))
        return timezone.now() - timedelta(seconds=max(0, timeout_seconds))

    @staticmethod
    def expire_stale_jobs(order_ids: list[int], job_action: str) -> int:
        """Đánh FAILED job PENDING/RUNNING quá hạn để lượt enqueue sau không bị coi là trùng.

        Trả về số job vừa bị đánh quá hạn.
        """
        stale_active_jobs = OrderKiotVietJob.objects.filter(
            order_id__in=order_ids,
            action=job_action,
            status__in=list(_ACTIVE_STATUSES),
            updated_at__lt=OrderKiotVietJobService._active_cutoff(),
        )
        stale_count = stale_active_jobs.count()
        if stale_count:
            now = timezone.now()
            stale_active_jobs.update(
                status=OrderKiotVietJobStatus.FAILED,
                error_message=_STALE_JOB_MESSAGE,
                finished_at=now,
                updated_at=now,
            )
            logger.warning(
                "Marked stale KiotViet jobs as failed before requeue: action=%s stale_count=%s",
                job_action,
                stale_count,
            )
        return stale_count

    @staticmethod
    def active_job_order_ids(order_ids: list[int], job_action: str) -> set[int]:
        """Đơn đang có job cùng action còn sống (PENDING/RUNNING trong cửa sổ timeout)."""
        return set(
            OrderKiotVietJob.objects.filter(
                order_id__in=order_ids,
                action=job_action,
                status__in=list(_ACTIVE_STATUSES),
                updated_at__gte=OrderKiotVietJobService._active_cutoff(),
            ).values_list("order_id", flat=True)
        )

    @staticmethod
    def enqueue(
        order_ids: list[int],
        job_action: str,
        requested_by,
        *,
        event_source: str,
        stagger_seconds: int | None = None,
        extra_fields_for: Callable[[int], dict] | None = None,
    ) -> KiotVietJobEnqueueResult:
        """Tạo job ``job_action`` cho từng đơn chưa có job sống rồi đẩy vào queue ``kiotviet``.

        - ``stagger_seconds``: giãn cách dispatch giữa các job (job thứ i chờ
          ``i * stagger``); ``None`` → ``settings.KIOTVIET_JOB_STAGGER_SECONDS``.
        - ``extra_fields_for(order_id)``: field bổ sung cho job của từng đơn (vd tham số
          KShip); ``None`` nếu job không cần gì ngoài ``order_id/action/requested_by``.
        - ``event_source``: nhãn nguồn ghi vào event ``order.updated`` phát cho cả lô.
        """
        OrderKiotVietJobService.expire_stale_jobs(order_ids, job_action)
        active_ids = OrderKiotVietJobService.active_job_order_ids(order_ids, job_action)
        jobs_to_create = [
            OrderKiotVietJob(
                order_id=order_id,
                action=job_action,
                requested_by=requested_by,
                **(extra_fields_for(order_id) if extra_fields_for is not None else {}),
            )
            for order_id in order_ids
            if order_id not in active_ids
        ]
        created_jobs = OrderKiotVietJob.objects.bulk_create(jobs_to_create)

        if stagger_seconds is None:
            stagger_seconds = int(getattr(settings, "KIOTVIET_JOB_STAGGER_SECONDS", 3))
        dispatch_failed_job_ids: list[int] = []
        for idx, job in enumerate(created_jobs):
            try:
                dispatch(
                    "kv.process_job",
                    queue="kiotviet",
                    countdown=idx * stagger_seconds,
                    job_id=job.id,
                )
            except Exception:
                logger.exception(
                    "Failed to dispatch job for OrderKiotVietJob id=%s action=%s", job.id, job_action
                )
                dispatch_failed_job_ids.append(job.id)

        if dispatch_failed_job_ids:
            now = timezone.now()
            OrderKiotVietJob.objects.filter(id__in=dispatch_failed_job_ids).update(
                status=OrderKiotVietJobStatus.FAILED,
                error_message=_DISPATCH_FAILED_MESSAGE,
                finished_at=now,
                updated_at=now,
            )

        if created_jobs:
            # Nạp staff_id + channel_code cho CẢ LÔ bằng một query. Thiếu hai field này
            # thì mỗi event buộc consumer tra DB cho TỪNG client đang kết nối — bulk 50
            # đơn × vài chục tab là đủ cạn max_connections (sự cố 2026-07-29, xem
            # docs/ws-broadcast-db-fanout-2026-07-29.md).
            event_order_ids = [job.order_id for job in created_jobs]
            order_meta = load_event_meta(event_order_ids)
            for order_id in event_order_ids:
                meta = order_meta.get(order_id) or {}
                OrderService.push_event(
                    OrderEventContract.build_order_updated(
                        order_id=order_id,
                        channel_code=meta.get("channel__code"),
                        staff_id=meta.get("staff_id"),
                        source=event_source,
                    )
                )

        return KiotVietJobEnqueueResult(
            created_jobs=list(created_jobs),
            dispatch_failed_job_ids=dispatch_failed_job_ids,
        )
