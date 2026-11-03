"""Endpoint báo cáo chi phí quảng cáo theo page.

Đặt trong app facebook_ads (tầng TRÊN orders theo backend/.importlinter) vì cần
dùng luật phân quyền của báo cáo ads bên orders. Đặt ngược lại là thêm nợ layering.
"""

from datetime import date as date_cls

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.facebook_ads.page_report import aggregate_page_cost
from apps.orders.repositories import OrderRepository
from apps.users.permissions import require_section


def _revenue_available_from() -> str | None:
    """Mốc go-live doanh thu theo page. None = giai đoạn A, chưa có attribution."""
    raw = getattr(settings, "FB_PAGE_REPORT_START_DATE", "") or ""
    return str(raw).strip() or None


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("ads-report")])
def page_cost_report(request):
    since_raw = (request.query_params.get("since") or "").strip()
    until_raw = (request.query_params.get("until") or "").strip()
    try:
        since = date_cls.fromisoformat(since_raw)
        until = date_cls.fromisoformat(until_raw)
    except ValueError:
        return Response(
            {"detail": "since/until phải theo định dạng YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if since > until:
        return Response(
            {"detail": "since phải trước hoặc bằng until."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account_ids = OrderRepository.accessible_ads_ad_account_ids(request.user)

    return Response({
        "since": since.isoformat(),
        "until": until.isoformat(),
        "dimension": "page",
        "revenue_available_from": _revenue_available_from(),
        "rows": aggregate_page_cost(since, until, account_ids),
    })
