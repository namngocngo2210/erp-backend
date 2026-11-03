"""Entry tự động vào flow tác nghiệp (spec §4.4) — 2 tầng: at-ingest + sweep."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.contact_flow.application.services.operation_engine import OperationEngine
from apps.orders.models import MARKETPLACE_ORDER_SOURCES, Order

logger = logging.getLogger(__name__)
_EXCLUDED_ORDER_SOURCES = [*MARKETPLACE_ORDER_SOURCES, "koc"]


class OperationEntryService:
    @staticmethod
    def enter_on_new_order(order) -> bool:
        if order.customer_id is None or order.order_source in _EXCLUDED_ORDER_SOURCES:
            return False
        state = OperationEngine.enter_start(order.customer)
        return state is not None

    # Trần mỗi tick (mirror SWEEP_MAX_ROWS_PER_TICK của floating pool): beat 5' nên
    # phần vượt trần tự dồn sang tick sau; không có trần thì một đợt import lớn
    # biến sweep thành N transaction + 5N query trong một tick (audit 2026-08-19 P1.6).
    SWEEP_MAX_ROWS_PER_TICK = 2000

    @staticmethod
    def sweep(hours: int = 24) -> dict:
        from apps.contact_flow.models import OperationStage

        # Stage bắt đầu là hằng số hệ thống — nạp MỘT lần cho cả lô thay vì tra
        # lại trong từng enter_start (trước fix: ~5 query/khách).
        start_stage = OperationStage.objects.filter(is_start=True, is_active=True).first()
        if start_stage is None:
            logger.warning("contact-flow entry sweep: no active start stage — skip")
            return {"created": 0, "skipped": "no_start_stage"}

        cutoff = timezone.now() - timedelta(hours=hours)
        customer_ids = list(
            Order.objects.filter(
                created_at__gte=cutoff, deleted_at__isnull=True, customer_id__isnull=False
            )
            .exclude(order_source__in=_EXCLUDED_ORDER_SOURCES)
            .exclude(customer__operation_state__isnull=False)
            .values_list("customer_id", flat=True)
            .distinct()[: OperationEntryService.SWEEP_MAX_ROWS_PER_TICK]
        )
        # Cả lô đi một transaction, 4 query (audit 2026-09-23 T1 §2.3) — trước đây
        # `enter_start` từng khách = 3 query + 1 transaction/khách (đo 33→63 query
        # khi 10→20 khách). Đường request (đơn mới) vẫn dùng `enter_start` lẻ.
        created = OperationEngine.enter_start_bulk(customer_ids, start_stage=start_stage)
        logger.info("contact-flow entry sweep: created=%s", created)
        return {"created": created}
