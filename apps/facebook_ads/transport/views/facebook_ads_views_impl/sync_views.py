"""View kích hoạt đồng bộ dữ liệu từ Facebook Graph API (TKQC + chi phí ngày)."""

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

from apps.facebook_ads.infrastructure.token_service import mark_token_invalid, mark_token_ok
from apps.facebook_ads.services import (
    FacebookGraphError,
    FacebookTokenError,
    VALID_DATE_PRESETS,
    sync_ad_accounts_for_app,
    sync_daily_spend_for_app,
)

from ._view_helpers import _get_app


# FE: nút "Đồng bộ TKQC" trên dashboard — trả về thống kê đồng bộ.
@extend_schema(
    request=None,
    responses={
        200: inline_serializer(
            "FacebookSyncAdAccountsResult",
            fields={
                "fetched": drf_serializers.IntegerField(),
                "created": drf_serializers.IntegerField(),
                "updated": drf_serializers.IntegerField(),
                "removed": drf_serializers.IntegerField(),
                "synced_at": drf_serializers.CharField(),
            },
        ),
        404: OpenApiResponse(description="Không tìm thấy FB App"),
        502: OpenApiResponse(description="Lỗi gọi Facebook Graph API"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("facebook-ads:edit")])
def sync_ad_accounts(request, app_id: int):
    app = _get_app(app_id)
    if app is None:
        return Response({"detail": "Không tìm thấy FB App."}, status=status.HTTP_404_NOT_FOUND)
    try:
        result = sync_ad_accounts_for_app(app)
    except FacebookTokenError as exc:
        mark_token_invalid(app, str(exc))
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    except FacebookGraphError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    mark_token_ok(app)
    return Response(result)


# FE: nút "Đồng bộ chi phí" — nhận date_preset/since/until, trả thống kê upsert.
@extend_schema(
    request=inline_serializer(
        "FacebookSyncDailySpendRequest",
        fields={
            "since": drf_serializers.CharField(required=False),
            "until": drf_serializers.CharField(required=False),
            "date_preset": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "FacebookSyncDailySpendResult",
            fields={
                "accounts": drf_serializers.IntegerField(),
                "rows_upserted": drf_serializers.IntegerField(),
                # Số account bị Graph API từ chối trong batch (vd token hết hạn).
                # Vắng mặt khi app không có account active nào (early-return).
                "failed_accounts": drf_serializers.IntegerField(required=False),
            },
        ),
        400: OpenApiResponse(description="date_preset không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy FB App"),
        502: OpenApiResponse(description="Lỗi gọi Facebook Graph API"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("facebook-ads:edit")])
def sync_daily_spend(request, app_id: int):
    """Trigger đồng bộ daily-spend on-demand.

    Hữu ích khi cron chưa chạy / data hôm nay chưa có. Nhận date_preset
    hoặc since/until từ query/body (giống endpoint daily-spend GET).
    """
    app = _get_app(app_id)
    if app is None:
        return Response({"detail": "Không tìm thấy FB App."}, status=status.HTTP_404_NOT_FOUND)

    source = request.data if isinstance(request.data, dict) else {}
    since = (source.get("since") or request.query_params.get("since") or "").strip()
    until = (source.get("until") or request.query_params.get("until") or "").strip()
    date_preset = (source.get("date_preset") or request.query_params.get("date_preset") or "").strip() or None

    try:
        if since and until:
            result = sync_daily_spend_for_app(app, since=since, until=until)
        elif date_preset:
            if date_preset not in VALID_DATE_PRESETS:
                return Response(
                    {"detail": f"date_preset không hợp lệ ({date_preset})."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            result = sync_daily_spend_for_app(app, date_preset=date_preset)
        else:
            result = sync_daily_spend_for_app(app)
    except FacebookTokenError as exc:
        # Đồng bộ tay là lúc người vận hành kiểm chứng token vừa dán — kết quả phải
        # được ghi lại y như lượt cron, không thì badge trên UI nói dối tới lượt sau.
        mark_token_invalid(app, str(exc))
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    except FacebookGraphError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    mark_token_ok(app)
    return Response(result)
