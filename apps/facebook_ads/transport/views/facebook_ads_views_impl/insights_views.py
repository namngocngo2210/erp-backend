"""View báo cáo insights lấy trực tiếp từ Graph API (một TKQC hoặc batch nhiều TKQC)."""

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

from apps.facebook_ads.models import FacebookAdAccount
from apps.facebook_ads.serializers import (
    FacebookAdAccountSerializer,
    FacebookInsightRowSerializer,
)
from apps.facebook_ads.services import (
    FacebookGraphError,
    VALID_DATE_PRESETS,
    VALID_INSIGHT_LEVELS,
    fetch_ad_account_insights,
    fetch_insights_batch,
)

from ._view_helpers import _get_app


# FE: báo cáo insights của 1 TKQC — wrapper {ad_account, ..., data[]}.
# `data` là các dòng insight từ Graph API (field tùy level — xem FacebookInsightRow).
@extend_schema(
    responses={
        200: inline_serializer(
            "FacebookAdAccountInsightsResponse",
            fields={
                "ad_account": FacebookAdAccountSerializer(),
                "level": drf_serializers.CharField(),
                "date_preset": drf_serializers.CharField(allow_null=True),
                "since": drf_serializers.CharField(allow_null=True),
                "until": drf_serializers.CharField(allow_null=True),
                "time_increment": drf_serializers.CharField(),
                "data": FacebookInsightRowSerializer(many=True),
            },
        ),
        400: OpenApiResponse(description="level/date_preset không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy TKQC"),
        502: OpenApiResponse(description="Lỗi gọi Facebook Graph API"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("facebook-ads")])
def ad_account_insights(request, ad_account_id: int):
    ad_account = (
        FacebookAdAccount.objects.select_related("app")
        .filter(id=ad_account_id, app__deleted_at__isnull=True)
        .first()
    )
    if ad_account is None:
        return Response({"detail": "Không tìm thấy TKQC."}, status=status.HTTP_404_NOT_FOUND)

    level = (request.query_params.get("level") or "campaign").strip()
    if level not in VALID_INSIGHT_LEVELS:
        return Response({"detail": f"level không hợp lệ ({level})."}, status=status.HTTP_400_BAD_REQUEST)

    since = (request.query_params.get("since") or "").strip()
    until = (request.query_params.get("until") or "").strip()
    date_preset = (request.query_params.get("date_preset") or "").strip() or None
    time_increment = (request.query_params.get("time_increment") or "1").strip()

    if since and until:
        date_preset = None
    else:
        since = until = ""
        if date_preset is None:
            date_preset = "last_30d"
        if date_preset not in VALID_DATE_PRESETS:
            return Response(
                {"detail": f"date_preset không hợp lệ ({date_preset})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        rows = fetch_ad_account_insights(
            ad_account,
            level=level,
            date_preset=date_preset,
            since=since or None,
            until=until or None,
            time_increment=time_increment,
        )
    except FacebookGraphError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response(
        {
            "ad_account": FacebookAdAccountSerializer(ad_account).data,
            "level": level,
            "date_preset": date_preset,
            "since": since or None,
            "until": until or None,
            "time_increment": time_increment,
            "data": rows,
        }
    )


# FE: lấy insights nhiều TKQC một lần — trả map {ad_account_id: insight}.
@extend_schema(
    request=inline_serializer(
        "FacebookInsightsBatchRequest",
        fields={
            "ad_account_ids": drf_serializers.ListField(
                child=drf_serializers.IntegerField()
            ),
            "level": drf_serializers.CharField(required=False),
            "since": drf_serializers.CharField(required=False),
            "until": drf_serializers.CharField(required=False),
            "date_preset": drf_serializers.CharField(required=False),
            "time_increment": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "FacebookInsightsBatchResponse",
            # `data` = map ad_account_id(str) -> danh sách dòng insight
            # (fetch_insights_batch trả dict[int, list[dict]]).
            fields={
                "data": drf_serializers.DictField(
                    child=FacebookInsightRowSerializer(many=True)
                ),
            },
        ),
        400: OpenApiResponse(description="ad_account_ids/level/date_preset không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy FB App"),
        502: OpenApiResponse(description="Lỗi gọi Facebook Graph API"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("facebook-ads")])
def insights_batch(request, app_id: int):
    app = _get_app(app_id)
    if app is None:
        return Response({"detail": "Không tìm thấy FB App."}, status=status.HTTP_404_NOT_FOUND)

    payload = request.data or {}
    raw_ids = payload.get("ad_account_ids") or []
    try:
        ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        return Response({"detail": "ad_account_ids phải là danh sách số nguyên."}, status=status.HTTP_400_BAD_REQUEST)

    accounts = list(FacebookAdAccount.objects.filter(app=app, id__in=ids))
    if not accounts:
        return Response({"data": {}})

    level = (payload.get("level") or "account").strip()
    if level not in VALID_INSIGHT_LEVELS:
        return Response({"detail": f"level không hợp lệ ({level})."}, status=status.HTTP_400_BAD_REQUEST)

    since = (payload.get("since") or "").strip()
    until = (payload.get("until") or "").strip()
    date_preset = (payload.get("date_preset") or "").strip() or None
    time_increment = (payload.get("time_increment") or "all_days").strip()

    if since and until:
        date_preset = None
    else:
        since = until = ""
        if date_preset is None:
            date_preset = "today"
        if date_preset not in VALID_DATE_PRESETS:
            return Response(
                {"detail": f"date_preset không hợp lệ ({date_preset})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        results = fetch_insights_batch(
            app,
            accounts,
            level=level,
            date_preset=date_preset,
            since=since or None,
            until=until or None,
            time_increment=time_increment,
        )
    except FacebookGraphError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response({"data": {str(k): v for k, v in results.items() if isinstance(k, int)}})
