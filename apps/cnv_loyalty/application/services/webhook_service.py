"""Ghi + áp webhook event CNV (idempotent), pattern `best_express.webhook_service`.

Tách hai bước có lý do vận hành: CNV đang chờ HTTP response, mà ingest một đơn
chạm nhiều bảng. Lưu thô rồi trả 200 ngay thì CNV không retry vì mình chậm; lỗi
áp event nằm lại ở ``processed=False`` cho cron
``retry_unprocessed_webhook_events`` vớt.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cnv_loyalty.application.services.inbound_service import (
    CnvOrderIngestService,
    extract_order_rows,
)
from apps.cnv_loyalty.models import CnvWebhookEvent

logger = logging.getLogger(__name__)

_ERROR_MAX_LEN = 500


def payload_hash(payload) -> str:
    """Hash ổn định của payload — khoá chống trùng khi CNV retry.

    ``sort_keys`` để thứ tự key trong JSON không tạo ra hash khác cho cùng một
    nội dung; ``default=str`` để một giá trị lạ không serialize được cũng không
    làm chết cả webhook.
    """
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_topic(request_headers, payload) -> str:
    """Topic lấy từ header nếu CNV có gửi, không thì từ payload.

    Không có danh sách topic hợp lệ nào được kiểm chứng (swagger khai
    `array<string>` trống), nên đây thuần tuý là NHÃN để truy vết — mọi quyết
    định xử lý dựa vào hình dạng payload, xem ``inbound_service._looks_like_order``.
    """
    for header in ("X-Cnv-Topic", "X-Topic", "X-Webhook-Topic", "X-Cnv-Event"):
        value = str(request_headers.get(header) or "").strip()
        if value:
            return value[:64]
    if isinstance(payload, dict):
        for key in ("topic", "event", "event_type"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value[:64]
    return ""


def store_event(payload, topic: str = "") -> CnvWebhookEvent | None:
    """Lưu event thô; trả None nếu đã có event trùng hệt (CNV retry)."""
    rows = extract_order_rows(payload)
    cnv_object_id = None
    if rows:
        try:
            cnv_object_id = int(rows[0].get("id"))
        except (TypeError, ValueError):
            cnv_object_id = None
    try:
        with transaction.atomic():
            return CnvWebhookEvent.objects.create(
                topic=topic,
                cnv_object_id=cnv_object_id,
                payload=payload if isinstance(payload, (dict, list)) else {"raw": payload},
                payload_hash=payload_hash(payload),
            )
    except IntegrityError:
        logger.info(
            "CNV webhook: bỏ event trùng topic=%s object_id=%s", topic or "-", cnv_object_id
        )
        return None


def apply_event(event: CnvWebhookEvent) -> bool:
    """Ingest payload của event. Trả True nếu xử lý xong.

    Event không chứa đơn nào KHÔNG được đánh ``processed=True``: đó là dấu hiệu
    CNV gửi topic mình chưa hỗ trợ, và im lặng nuốt nó là mất luôn manh mối. Để
    lại ``processed=False`` kèm ``last_error`` để người vận hành thấy được.
    """
    rows = extract_order_rows(event.payload)
    event.attempts += 1
    if not rows:
        event.last_error = (
            "payload không chứa đơn nào — topic chưa hỗ trợ? "
            f"keys={sorted(event.payload.keys()) if isinstance(event.payload, dict) else type(event.payload).__name__}"
        )[:_ERROR_MAX_LEN]
        event.save(update_fields=["attempts", "last_error"])
        logger.warning("CNV webhook: event#%s không nhận diện được đơn", event.pk)
        return False

    try:
        stats = CnvOrderIngestService().ingest_rows(rows)
    except Exception as exc:  # noqa: BLE001 — lỗi nằm ở event, cron retry
        event.last_error = str(exc)[:_ERROR_MAX_LEN]
        event.save(update_fields=["attempts", "last_error"])
        logger.exception("CNV webhook: apply_event lỗi (event#%s)", event.pk)
        return False

    if stats.failed:
        event.last_error = "; ".join(stats.errors)[:_ERROR_MAX_LEN]
        event.save(update_fields=["attempts", "last_error"])
        return False

    event.processed = True
    event.processed_at = timezone.now()
    event.last_error = ""
    event.save(update_fields=["attempts", "processed", "processed_at", "last_error"])
    logger.info("CNV webhook: event#%s áp xong %s", event.pk, stats.as_dict())
    return True
