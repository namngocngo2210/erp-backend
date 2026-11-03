"""Nhận trạng thái vận đơn Best realtime cho hành trình bảo hành.

`best_express` publish `BestTrackingUpdated` (từ webhook và từ trace sync);
warranty subscribe ở đây. Chiều phụ thuộc chỉ đi warranty → best_express.contracts,
không có chiều ngược lại. Cron `warranty-sync-best-tracking` vẫn chạy 10 phút/lần
làm lưới an toàn cho event rơi hoặc mã không thuộc account đã cấu hình webhook.
"""

from __future__ import annotations

import logging

from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.common.events import event_bus

logger = logging.getLogger(__name__)


def _on_best_tracking_updated(event: BestTrackingUpdated) -> None:
    from django.db.models import Q
    from django.utils import timezone

    from apps.warranty.models import WarrantyJourney
    from apps.warranty.tracking_status import (
        JOURNEY_CODE_FIELDS,
        map_best_vc_group,
        should_apply_best_status,
    )

    status = (event.best_status_code or "").strip()
    codes = [
        code
        for code in ((event.code_best or "").strip(), (event.merchant_order_code or "").strip())
        if code
    ]
    if not codes or not status:
        return
    # Mã lạ (Best thêm mã mới, hoặc mã hủy "2" không có trong bảng nhóm hiển thị
    # BH) → bỏ qua toàn bộ, không ghi status lẫn ms. Ghi mã thô sẽ khiến
    # `_group_for` fallback về "created", kéo badge "GTC" lùi về "Tạo mới" —
    # xem review 2026-07-22 finding #4.
    if map_best_vc_group(status) is None:
        logger.info("best tracking event: mã trạng thái lạ %r — bỏ qua", status)
        return

    incoming_ms = int(event.modified_at_ms or 0)
    now = timezone.now()

    match = Q()
    for code_field, _, _ in JOURNEY_CODE_FIELDS:
        match |= Q(**{f"{code_field}__in": codes})

    # Không log khi không khớp: đa số event Best là của đơn bán, không phải bảo
    # hành — log ở đây sẽ thành rác.
    for journey in WarrantyJourney.objects.filter(match):
        for code_field, status_field, ms_field in JOURNEY_CODE_FIELDS:
            if (getattr(journey, code_field) or "").strip() not in codes:
                continue
            # Bộ lọc sớm dựa trên bản đọc có thể đã cũ — chỉ để tránh bắn UPDATE
            # vô ích, KHÔNG phải trọng tài chống race (xem docstring hàm).
            if not should_apply_best_status(getattr(journey, ms_field), incoming_ms):
                continue
            # UPDATE có điều kiện ngay ở tầng DB: 2 worker/tiến trình nhận 2
            # event của cùng 1 mã gần như đồng thời vẫn không lệch nhau, vì
            # điều kiện `ms_field__lte` được Postgres đánh giá trên giá trị
            # ĐANG CÓ tại thời điểm ghi (có khoá dòng), không phải bản đọc cũ
            # trong vòng lặp này — đóng đúng race ở review 2026-07-22 finding #2.
            WarrantyJourney.objects.filter(
                pk=journey.pk, **{f"{ms_field}__lte": incoming_ms}
            ).update(
                **{
                    status_field: status,
                    ms_field: incoming_ms,
                    "tracking_synced_at": now,
                    # `.update()` bỏ qua `auto_now` — phải set tay, nếu không
                    # `useJourneyForm` phía frontend (đồng bộ theo `updated_at`)
                    # sẽ đứng im dù badge đã đổi.
                    "updated_at": now,
                }
            )


def register() -> None:
    if not event_bus.is_subscribed(
        BestTrackingUpdated.event_type, _on_best_tracking_updated
    ):
        event_bus.subscribe(BestTrackingUpdated.event_type, _on_best_tracking_updated)
