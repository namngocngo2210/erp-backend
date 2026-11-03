"""View kích hoạt đồng bộ dữ liệu từ Google Ads API (tài khoản + chi phí ngày)."""

from datetime import date as date_cls

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

from apps.users.permissions import require_permission

from apps.google_ads.services import (
    GoogleAdsApiError,
    sync_accounts_for_config,
    sync_daily_spend_for_config,
)

from ._view_helpers import PRESET_TO_RANGE, SECTION, _get_config


# FE: nút "Đồng bộ tài khoản" — trả về thống kê đồng bộ.
@extend_schema(
    request=None,
    responses={
        200: inline_serializer(
            "GoogleAdsSyncAccountsResult",
            fields={
                "fetched": drf_serializers.IntegerField(),
                "created": drf_serializers.IntegerField(),
                "updated": drf_serializers.IntegerField(),
                "removed": drf_serializers.IntegerField(),
                "synced_at": drf_serializers.CharField(),
            },
        ),
        404: OpenApiResponse(description="Không tìm thấy cấu hình Google Ads"),
        502: OpenApiResponse(description="Lỗi gọi Google Ads API"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(f"{SECTION}:edit")])
def sync_accounts(request, config_id: int):
    config = _get_config(config_id)
    if config is None:
        return Response({"detail": "Không tìm thấy cấu hình Google Ads."}, status=status.HTTP_404_NOT_FOUND)
    try:
        result = sync_accounts_for_config(config)
    except GoogleAdsApiError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return Response(result)


# FE: nút "Đồng bộ chi phí" — nhận date_preset/since/until, trả thống kê upsert.
@extend_schema(
    request=inline_serializer(
        "GoogleAdsSyncDailySpendRequest",
        fields={
            "since": drf_serializers.CharField(required=False),
            "until": drf_serializers.CharField(required=False),
            "date_preset": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "GoogleAdsSyncDailySpendResult",
            fields={
                "accounts": drf_serializers.IntegerField(),
                "rows_upserted": drf_serializers.IntegerField(),
            },
        ),
        400: OpenApiResponse(description="date_preset/since/until không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy cấu hình Google Ads"),
        502: OpenApiResponse(description="Lỗi gọi Google Ads API"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(f"{SECTION}:edit")])
def sync_daily_spend(request, config_id: int):
    config = _get_config(config_id)
    if config is None:
        return Response({"detail": "Không tìm thấy cấu hình Google Ads."}, status=status.HTTP_404_NOT_FOUND)

    source = request.data if isinstance(request.data, dict) else {}
    since = (source.get("since") or request.query_params.get("since") or "").strip()
    until = (source.get("until") or request.query_params.get("until") or "").strip()
    preset = (source.get("date_preset") or request.query_params.get("date_preset") or "").strip() or None

    # Service chỉ nhận since/until → resolve preset sang range trước khi gọi.
    if not (since and until) and preset:
        if preset not in PRESET_TO_RANGE:
            return Response({"detail": f"date_preset không hợp lệ ({preset})."}, status=status.HTTP_400_BAD_REQUEST)
        s, u = PRESET_TO_RANGE[preset](timezone.localdate())
        since, until = s.isoformat(), u.isoformat()

    if since and until:
        try:
            date_cls.fromisoformat(since)
            date_cls.fromisoformat(until)
        except ValueError:
            return Response(
                {"detail": "since/until phải có dạng YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        if since and until:
            result = sync_daily_spend_for_config(config, since=since, until=until)
        else:
            result = sync_daily_spend_for_config(config)
    except GoogleAdsApiError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return Response(result)
