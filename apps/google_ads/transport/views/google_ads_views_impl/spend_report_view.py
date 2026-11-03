"""View báo cáo chi phí Google Ads đọc từ bảng spend đã đồng bộ sẵn."""

from datetime import date as date_cls

from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_section

from apps.google_ads.models import GoogleAdsAccountDailySpend

from ._view_helpers import PRESET_TO_RANGE, SECTION, _get_config


# FE: tổng hợp chi phí theo customer — by_account là map id -> {spend,...}.
@extend_schema(
    responses={
        200: inline_serializer(
            "GoogleAdsDailySpendSummaryResponse",
            fields={
                "since": drf_serializers.CharField(),
                "until": drf_serializers.CharField(),
                "by_account": drf_serializers.DictField(
                    child=inline_serializer(
                        "GoogleAdsDailySpendByAccount",
                        fields={
                            "spend": drf_serializers.CharField(),
                            "impressions": drf_serializers.IntegerField(),
                            "clicks": drf_serializers.IntegerField(),
                        },
                    )
                ),
                "last_synced_at": drf_serializers.CharField(allow_null=True),
            },
        ),
        400: OpenApiResponse(description="Ngày/preset/account_ids không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy cấu hình Google Ads"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def daily_spend_summary(request, config_id: int):
    """Tổng hợp chi phí theo từng customer của một config trong khoảng thời gian.

    Mirror của FB daily_spend_summary. Nhận date_preset hoặc since/until,
    đọc từ bảng GoogleAdsAccountDailySpend (do cron/sync đã ghi sẵn).
    """
    config = _get_config(config_id)
    if config is None:
        return Response({"detail": "Không tìm thấy cấu hình Google Ads."}, status=status.HTTP_404_NOT_FOUND)

    today = timezone.localdate()
    since_str = (request.query_params.get("since") or "").strip()
    until_str = (request.query_params.get("until") or "").strip()
    preset = (request.query_params.get("date_preset") or "").strip() or None

    if since_str and until_str:
        try:
            since = date_cls.fromisoformat(since_str)
            until = date_cls.fromisoformat(until_str)
        except ValueError:
            return Response({"detail": "Ngày không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        preset = preset or "today"
        if preset not in PRESET_TO_RANGE:
            return Response({"detail": f"preset không hỗ trợ: {preset}"}, status=status.HTTP_400_BAD_REQUEST)
        since, until = PRESET_TO_RANGE[preset](today)

    account_ids_raw = request.query_params.get("account_ids", "")
    id_filter: list[int] | None = None
    if account_ids_raw.strip():
        try:
            id_filter = [int(x) for x in account_ids_raw.split(",") if x.strip()]
        except ValueError:
            return Response({"detail": "account_ids phải là danh sách số nguyên."}, status=status.HTTP_400_BAD_REQUEST)

    qs = GoogleAdsAccountDailySpend.objects.filter(
        ad_account__config=config,
        date__gte=since,
        date__lte=until,
    )
    if id_filter is not None:
        qs = qs.filter(ad_account_id__in=id_filter)

    agg = qs.values("ad_account_id").annotate(
        spend=Sum("spend"),
        impressions=Sum("impressions"),
        clicks=Sum("clicks"),
    )
    by_acc = {
        row["ad_account_id"]: {
            "spend": str(row["spend"] or 0),
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
        }
        for row in agg
    }

    last_synced = qs.order_by("-last_synced_at").values_list("last_synced_at", flat=True).first()
    return Response({
        "since": since.isoformat(),
        "until": until.isoformat(),
        "by_account": by_acc,
        "last_synced_at": last_synced.isoformat() if last_synced else None,
    })
