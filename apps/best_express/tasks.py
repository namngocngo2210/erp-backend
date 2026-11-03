"""Cron của Best Express: retention cho bảng event webhook.

Theo đúng pattern `apps.audit.tasks.purge_old_audit_logs` / `purge_matched_pending_scans`:
single-flight lock + xoá theo lô.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.db import OperationalError
from django.conf import settings
from django.utils import timezone

from apps.common.db_guards import single_flight

logger = logging.getLogger(__name__)

_PURGE_LOCK_KEY = "lock:purge_best_webhook_events"
_PURGE_LOCK_TTL = 900  # > time_limit=600
_PURGE_BATCH_SIZE = 10_000

_RETRY_LOCK_KEY = "lock:retry_best_webhook_events"
_RETRY_LOCK_TTL = 300  # > time_limit=240
_RETRY_MAX_EVENTS = 500


@shared_task(
    name="apps.best_express.tasks.purge_old_best_webhook_events",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=540,
    time_limit=600,
)
def purge_old_best_webhook_events(batch_size: int = _PURGE_BATCH_SIZE) -> int:
    """Xoá BestWebhookEvent cũ hơn BEST_WEBHOOK_EVENT_RETENTION_DAYS (env, default 90).

    Mỗi vận đơn BEST sinh ~10 event trạng thái, mỗi event kèm payload jsonb → bảng
    phình vô hạn nếu không dọn (audit 2026-07-14). Event ĐÃ xử lý xong hết giá trị sau
    khi shipment đã mang trạng thái mới nhất; event lỗi/chưa khớp (`processed=False`)
    được GIỮ LẠI bất kể tuổi để người/cron còn xử lý — chúng là bằng chứng dữ liệu chưa
    được áp.
    """
    from apps.best_express.models import BestWebhookEvent

    retention_days = int(getattr(settings, "BEST_WEBHOOK_EVENT_RETENTION_DAYS", 90))
    if retention_days <= 0:
        logger.info("best webhook event purge disabled (BEST_WEBHOOK_EVENT_RETENTION_DAYS<=0)")
        return 0

    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted_total = 0
    with single_flight(_PURGE_LOCK_KEY, _PURGE_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("purge_old_best_webhook_events already running — skip this cycle")
            return -1
        while True:
            ids = list(
                BestWebhookEvent.objects.filter(processed=True, received_at__lt=cutoff)
                .order_by()
                .values_list("id", flat=True)[:batch_size]
            )
            if not ids:
                break
            deleted, _ = BestWebhookEvent.objects.filter(id__in=ids).delete()
            deleted_total += deleted
    logger.info(
        "purged %s best webhook events older than %s days", deleted_total, retention_days
    )
    return deleted_total


@shared_task(
    name="apps.best_express.tasks.retry_unprocessed_best_webhook_events",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=200,
    time_limit=240,
)
def retry_unprocessed_best_webhook_events(max_events: int = _RETRY_MAX_EVENTS) -> dict:
    """Áp lại các event webhook chưa xử lý được (`processed=False`).

    Event rơi vào đây khi tới TRƯỚC lúc shipment được tạo (unmatched) hoặc khi
    `apply_event` ném lỗi. Không có cron này thì event nằm chết trong bảng dù webhook đã
    trả `Result 1` cho Best (không retry nữa) — trạng thái vận đơn mất vĩnh viễn.

    BỎ CUỘC theo tuổi: event quá `BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS` (mặc định 3
    ngày) gần như chắc chắn là vận đơn không thuộc hệ thống (mã lạ / account khác) —
    đánh dấu `processed=True` kèm lý do để purge dọn được. Nếu không, mỗi vòng cron sẽ
    lấy đúng N event chết cũ nhất, event MỚI (có thể áp được) không bao giờ tới lượt và
    bảng phình vô hạn (audit 2026-07-14 vòng 2).
    """
    from apps.best_express.application.services.webhook_service import apply_event
    from apps.best_express.models import BestWebhookEvent

    max_age_days = int(getattr(settings, "BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS", 3))
    applied = 0
    failed = 0
    abandoned = 0
    with single_flight(_RETRY_LOCK_KEY, _RETRY_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("retry_unprocessed_best_webhook_events already running — skip")
            return {"applied": -1, "failed": 0, "abandoned": 0, "pending": -1}

        events = list(
            BestWebhookEvent.objects.filter(processed=False).order_by("received_at")[:max_events]
        )
        errored_now: list[int] = []
        for event in events:
            try:
                if apply_event(event):
                    applied += 1
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001 — 1 event hỏng không chặn event khác
                failed += 1
                logger.exception(
                    "Best webhook retry: apply_event lỗi (event_id=%s)", event.pk
                )
                # apply_event ném exception thì transaction của nó rollback →
                # processing_error vẫn RỖNG. Không ghi vết thì event này bất tử:
                # abandon bỏ qua (không phải "unmatched"), purge bỏ qua
                # (processed=False) → bảng phình + chiếm slot retry vĩnh viễn
                # (audit 2026-07-14 vòng 4). Ghi ngoài transaction đã rollback.
                #
                # Chỉ miễn-abandon cho lỗi LẦN ĐẦU THẬT (error cũ trên chính event
                # còn rỗng): dùng "lỗi trong vòng này" thì event lỗi deterministic
                # (payload hỏng, vi phạm constraint) lại được miễn MỖI vòng — nó
                # luôn là oldest, luôn vào batch, luôn raise → bất tử trở lại,
                # đúng cái đang diệt (audit 2026-07-14 vòng 5).
                if not event.processing_error:
                    errored_now.append(event.pk)
                try:
                    BestWebhookEvent.objects.filter(pk=event.pk).update(
                        processing_error=f"error: {exc}"[:500]
                    )
                except Exception:  # noqa: BLE001 — DB chết thì đừng giết cả batch
                    logger.exception(
                        "Best webhook retry: không ghi được processing_error (event_id=%s)",
                        event.pk,
                    )

        # BỎ CUỘC — chạy SAU vòng áp lại, và CHỈ với event ĐÃ ĐƯỢC THỬ (processing_error
        # không rỗng: "unmatched" từ apply_event, "error:" từ nhánh exception phía trên).
        # Trước đây bước này chạy TRƯỚC vòng áp và quét mọi `processed=False` theo tuổi:
        # hệ thống chết vài ngày (worker treo, DB cạn connection) là vòng cron ĐẦU TIÊN
        # khi sống lại đánh dấu processed=True cho cả event chưa từng được thử — gồm cả
        # Giao thành công / COD / Chuyển hoàn — mà Best thì không đẩy lại. Mất trạng thái
        # vĩnh viễn (audit 2026-07-14 vòng 3). Event chưa thử (error rỗng) không bao giờ
        # bị bỏ cuộc.
        if max_age_days > 0:
            give_up_before = timezone.now() - timedelta(days=max_age_days)
            abandoned = (
                BestWebhookEvent.objects.filter(
                    processed=False, received_at__lt=give_up_before
                )
                # Chưa thử (error rỗng) hoặc vừa lỗi lần ĐẦU trong chính vòng này →
                # chưa bỏ cuộc, để dành ít nhất một vòng retry nữa.
                .exclude(processing_error="")
                .exclude(pk__in=errored_now)
                .update(
                    processed=True,
                    processing_error=(
                        f"abandoned: không áp được sau {max_age_days} ngày"
                    ),
                )
            )
            if abandoned:
                logger.warning(
                    "best webhook retry: bỏ cuộc %s event unmatched quá %s ngày",
                    abandoned, max_age_days,
                )

    pending = BestWebhookEvent.objects.filter(processed=False).count()
    logger.info(
        "best webhook retry: applied=%s failed=%s abandoned=%s still_pending=%s",
        applied,
        failed,
        abandoned,
        pending,
    )
    return {
        "applied": applied,
        "failed": failed,
        "abandoned": abandoned,
        "pending": pending,
    }
