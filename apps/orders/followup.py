# backend/apps/orders/followup.py
"""Suy nhiệt độ follow-up (real-time) từ số lần gọi + thời gian + trạng thái sale."""
from __future__ import annotations

from django.db.models import Count, IntegerField, Max, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce

from apps.orders.models import OrderJourneyEvent
from apps.orders.sheet_rules import (
    FOLLOWUP_STALE_DAYS,
    POSITIVE_SALES_STATUSES,
    SALE_CONTACT_EVENT,
)

HOT = "hot"
WARM = "warm"
COLD = "cold"


def compute_temperature(*, contact_count: int, days_since: int, sales_status: str | None) -> str:
    """Quy tắc (kiểm tra Lạnh trước, rồi Nóng, còn lại Ấm):
    - Lạnh nếu gọi > 5 lần HOẶC > 7 ngày không liên hệ.
    - Nóng nếu gọi ≤ 2 VÀ ≤ 3 ngày VÀ trạng thái tích cực.
    - Còn lại Ấm.
    """
    if contact_count > 5 or days_since > FOLLOWUP_STALE_DAYS:
        return COLD
    if contact_count <= 2 and days_since <= 3 and (sales_status in POSITIVE_SALES_STATUSES):
        return HOT
    return WARM


def annotate_contact_aggregates(queryset: QuerySet) -> QuerySet:
    """Annotate `contact_count` và `last_contact_at` từ OrderJourneyEvent(sale_contact).

    Dùng Subquery thay cho Count/Max trên JOIN: tránh GROUP BY trên toàn bộ
    queryset (vốn rất nặng khi đã select_related nhiều bảng + slice sau), đồng
    thời chỉ tính cho các dòng thực sự trả về. Kết quả giữ nguyên (contact_count,
    last_contact_at) nên mọi nơi gọi .values()/.order_by()/.filter() không đổi.
    """
    contacts = OrderJourneyEvent.objects.filter(
        order_id=OuterRef("pk"), event_type=SALE_CONTACT_EVENT
    )
    count_subquery = (
        contacts.order_by().values("order_id").annotate(c=Count("id")).values("c")
    )
    last_subquery = (
        contacts.order_by().values("order_id").annotate(m=Max("created_at")).values("m")
    )
    return queryset.annotate(
        contact_count=Coalesce(Subquery(count_subquery, output_field=IntegerField()), 0),
        last_contact_at=Subquery(last_subquery),
    )
