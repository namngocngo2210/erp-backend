"""Endpoint báo cáo chi phí quảng cáo theo chiến dịch (Facebook)."""

from datetime import date as date_cls

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.facebook_ads.campaign_report import aggregate_campaign_cost
from apps.orders.repositories import OrderRepository
from apps.users.permissions import require_section

# Chặn payload phình khi quét hết TKQC. Cắt mà KHÔNG nói ra thì bảng "thiếu chiến
# dịch" trông y hệt bảng "chiến dịch không tiêu tiền" — nên trả kèm cờ `truncated`.
MAX_ROWS = 1000


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("ads-report")])
def campaign_cost_report(request):
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

    scope = OrderRepository.ads_ad_account_scope(request.user)
    rows = aggregate_campaign_cost(since, until, scope.ids)

    return Response({
        "since": since.isoformat(),
        "until": until.isoformat(),
        "dimension": "campaign",
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        # null = không bị lọc. Số 0 nghĩa là user KHÔNG được gán TKQC nào — hai
        # trạng thái này cùng cho ra bảng rỗng nên phải phân biệt được ở client,
        # nếu không "không tiêu đồng nào" và "không có quyền" trông y hệt nhau.
        "accessible_accounts": scope.count,
    })
