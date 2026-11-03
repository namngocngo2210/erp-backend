"""Ghi + áp webhook event Best Express vào BestShipment (idempotent).

Đường áp event dùng chung cho webhook realtime và TraceQuery sync
(tracking_service) — một chỗ duy nhất quyết định trạng thái/phí.
"""

import logging
from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.best_express.domain.status_map import map_best_status
from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent
from apps.common.events import event_bus

logger = logging.getLogger(__name__)

_DECIMAL_FIELDS = (
    "cod",
    "product_price",
    "freight_fee",
    "cod_fee",
    "insurance_fee",
    "vat_fee",
    "return_fee",
    "discount",
    "total_fee",
)
_INT_FIELDS = ("weight", "length", "width", "height")


def store_event(
    account: BestExpressAccount | None, payload: dict
) -> BestWebhookEvent | None:
    """Lưu event thô; trả None nếu đã có event trùng (code, status, modified_at)."""
    code_best = str(payload.get("code_best") or "").strip()
    merchant_order_code = str(payload.get("merchant_order_code") or "").strip()
    best_status_code = str(payload.get("status") or "").strip()
    try:
        modified_at_ms = int(payload.get("modified_at") or 0)
    except (TypeError, ValueError):
        modified_at_ms = 0
    if not code_best and not merchant_order_code:
        # Không có identifier nào → không khớp partial unique constraint nào,
        # mỗi lần Best retry lại chèn thêm một dòng trùng. Bỏ qua, không lưu —
        # event không mã cũng không bao giờ khớp được shipment để áp.
        logger.warning(
            "Best webhook: bỏ qua event thiếu cả code_best lẫn merchant_order_code — "
            "kiểm tra schema payload (keys=%s)",
            sorted(payload.keys()),
        )
        return None
    try:
        with transaction.atomic():
            event = BestWebhookEvent.objects.create(
                account=account,
                code_best=code_best,
                merchant_order_code=merchant_order_code,
                best_status_code=best_status_code,
                payload=payload,
                modified_at_ms=modified_at_ms,
            )
    except IntegrityError:
        logger.info(
            "Best webhook: bỏ qua event trùng code=%s status=%s ts=%s",
            code_best,
            best_status_code,
            modified_at_ms,
        )
        return None
    # Event MỚI → báo module khác (hành trình bảo hành). Publish ở ĐÂY chứ không
    # ở apply_event: apply_event chỉ chạy khi khớp BestShipment, mà mã bảo hành
    # nhập tay không có dòng nào trong bảng đó. publish_on_commit để consumer
    # đọc được dữ liệu đã commit.
    event_bus.publish_on_commit(
        BestTrackingUpdated(
            code_best=code_best,
            merchant_order_code=merchant_order_code,
            best_status_code=best_status_code,
            modified_at_ms=modified_at_ms,
        )
    )
    return event


def _event_datetime(event: BestWebhookEvent) -> datetime | None:
    if not event.modified_at_ms:
        return None
    return datetime.fromtimestamp(event.modified_at_ms / 1000, tz=UTC)


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def apply_event(event: BestWebhookEvent) -> bool:
    """Áp event vào shipment khớp mã. True = shipment được cập nhật."""
    with transaction.atomic():
        # Chỉ tìm trong phạm vi account đã xác thực bằng token của event: token của
        # account A không được ghi đè trạng thái/phí shipment của account B
        # (tenant-scoping — audit 2026-07-14). Event không gắn account (đường trace nội
        # bộ) giữ nguyên phạm vi toàn cục.
        scoped = BestShipment.objects.select_for_update()
        if event.account_id is not None:
            scoped = scoped.filter(account_id=event.account_id)

        shipment = None
        if event.code_best:
            shipment = scoped.filter(code_best=event.code_best).first()
        if shipment is None and event.merchant_order_code:
            shipment = scoped.filter(merchant_order_code=event.merchant_order_code).first()
        if shipment is None:
            event.processed = False
            event.processing_error = "unmatched: không tìm thấy BestShipment"
            event.save(update_fields=["processed", "processing_error"])
            return False

        event_dt = _event_datetime(event)
        if shipment.last_event_at and (event_dt is None or event_dt < shipment.last_event_at):
            event.processed = True
            event.processing_error = (
                "stale: thiếu modified_at"
                if event_dt is None
                else "stale: event cũ hơn last_event_at (out-of-order)"
            )
            event.save(update_fields=["processed", "processing_error"])
            return False

        payload = event.payload or {}
        mapped = map_best_status(event.best_status_code)
        if mapped is not None:
            shipment.status = mapped
        else:
            logger.warning(
                "Best webhook: mã trạng thái lạ %s (code_best=%s) — giữ status nội bộ",
                event.best_status_code,
                event.code_best,
            )
        shipment.best_status_code = event.best_status_code
        content = str(payload.get("status_content") or "").strip()
        if content:
            shipment.status_content = content
        for field in _DECIMAL_FIELDS:
            value = _to_decimal(payload.get(field))
            if value is not None:
                setattr(shipment, field, value)
        for field in _INT_FIELDS:
            value = payload.get(field)
            if isinstance(value, (int, float)) and value:
                setattr(shipment, field, int(value))
        if event.code_best and not shipment.code_best:
            shipment.code_best = event.code_best
        if event_dt:
            shipment.last_event_at = event_dt
        shipment.save()

        event.processed = True
        event.processing_error = ""
        event.save(update_fields=["processed", "processing_error"])
        return True
