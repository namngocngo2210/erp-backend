"""Thống kê cơ hội resale Foxia cho trang Chia đơn (docs/foxia-resale-delivered.md).

Chỉ ĐỌC dữ liệu của ta (`foxia_resale_pushes` + ứng viên chờ), không gọi Foxia.
Quyền theo trang Chia đơn (`lead-assignment:view`) — người xem trang này là người
cấu hình cột Resale, cần thấy cơ hội đang chờ vì sao và đã chia cho ai.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.foxia.application.services.resale_push_service import FoxiaResalePushService
from apps.foxia.models import FoxiaResalePushStatus
from apps.users.permissions import require_permission

_STATES = ["ready", "disabled", "bad_since", "outside_shift", "no_resale_config"]


@extend_schema(
    parameters=[OpenApiParameter("date", str, description="YYYY-MM-DD, mặc định hôm nay")],
    responses={
        200: inline_serializer(
            "FoxiaResaleStatsResponse",
            fields={
                "date": drf_serializers.CharField(),
                # Hai trường dưới là trạng thái HIỆN TẠI, không phụ thuộc `date`.
                # CharField (không ChoiceField): enum tên `state` đụng `StateEnum` sẵn có
                # của spectacular và làm đổi tên enum cũ trong schema FE.
                "state": drf_serializers.CharField(help_text=" | ".join(_STATES)),
                "waiting": drf_serializers.IntegerField(),
                "by_status": drf_serializers.DictField(child=drf_serializers.IntegerField()),
                "by_staff": inline_serializer(
                    "FoxiaResaleStaffItem",
                    many=True,
                    fields={
                        "user_id": drf_serializers.IntegerField(),
                        "username": drf_serializers.CharField(),
                        "display_name": drf_serializers.CharField(),
                        "total": drf_serializers.IntegerField(),
                        "ok": drf_serializers.IntegerField(),
                        "failed": drf_serializers.IntegerField(),
                        "pending": drf_serializers.IntegerField(),
                    },
                ),
            },
        ),
        400: OpenApiResponse(description="date sai định dạng"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_permission("lead-assignment:view")])
def resale_stats(request):
    raw = request.query_params.get("date")
    try:
        day = date.fromisoformat(raw) if raw else timezone.localdate()
    except ValueError:
        return Response({"detail": "date phải theo định dạng YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

    service = FoxiaResalePushService()
    summary = service.day_summary(day)
    return Response(
        {
            "date": day.isoformat(),
            "state": service.state(),
            "waiting": service.waiting_count(),
            "by_status": {key: summary["by_status"].get(key, 0) for key in FoxiaResalePushStatus.values},
            "by_staff": summary["by_staff"],
        }
    )
