"""API của tích hợp Foxia: báo cáo hiệu quả marketing + quản lý ánh xạ nguồn.

File này KHÔNG có endpoint nào ghi sang Foxia — mọi thứ ở đây đọc/ghi dữ liệu
CỦA TA (báo cáo, bảng ánh xạ nguồn ↔ kênh nội bộ). Từ spec 2026-09-09, hệ THẬT
SỰ có một chiều ghi sang Foxia (đẩy khách của đơn landing), nhưng nó không đi
qua view nào ở đây: xem `apps/foxia/application/services/order_push_service.py`
(`FoxiaOrderPushService.push`, chạy trong task Celery `push_order_to_foxia`,
kích hoạt từ `apps.orders` qua `apps.dispatch`, kill switch `FOXIA_PUSH_ENABLED`)
và cấu hình bảng liên kết ở `apps/foxia/transport/link_config_views.py`.
"""

from __future__ import annotations

from datetime import date, timedelta

from drf_spectacular.utils import inline_serializer, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from rest_framework.response import Response

from apps.foxia.application.services import report_service
from apps.foxia.models import (
    FoxiaChannel,
    FoxiaChannelMap,
    FoxiaChannelMapKind,
    FoxiaSource,
    FoxiaSyncRun,
)
from apps.users.permissions import require_permission, require_section

SECTION = "foxia-report"
_MAX_RANGE_DAYS = 366


def _parse_range(request) -> tuple[date, date] | Response:
    """Đọc khoảng ngày từ query param, mặc định 30 ngày gần nhất.

    Chặn trần 366 ngày: bảng đơn mirror có thể rất lớn và endpoint này quét theo
    khoảng — không giới hạn là mời một câu query quét cả bảng vào giờ cao điểm.
    """
    today = timezone.localdate()
    raw_from = request.query_params.get("date_from")
    raw_to = request.query_params.get("date_to")
    try:
        date_to = date.fromisoformat(raw_to) if raw_to else today
        date_from = date.fromisoformat(raw_from) if raw_from else date_to - timedelta(days=29)
    except ValueError:
        return Response(
            {"detail": "date_from/date_to phải theo định dạng YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if date_from > date_to:
        return Response(
            {"detail": "date_from không được sau date_to."}, status=status.HTTP_400_BAD_REQUEST
        )
    if (date_to - date_from).days > _MAX_RANGE_DAYS:
        return Response(
            {"detail": f"Khoảng ngày tối đa {_MAX_RANGE_DAYS} ngày."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return date_from, date_to


@extend_schema(
    parameters=[
        OpenApiParameter("date_from", str, description="YYYY-MM-DD, mặc định 30 ngày trước"),
        OpenApiParameter("date_to", str, description="YYYY-MM-DD, mặc định hôm nay"),
        OpenApiParameter(
            "group_by", str, description="source | channel | marketer | branch | brand"
        ),
    ],
    responses={200: OpenApiResponse(description="Số liệu hiệu quả marketing Foxia")},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def marketing_report(request):
    parsed = _parse_range(request)
    if isinstance(parsed, Response):
        return parsed
    date_from, date_to = parsed
    group_by = request.query_params.get("group_by", "source")
    try:
        data = report_service.compute(date_from, date_to, group_by=group_by)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(data)


@extend_schema(responses={200: OpenApiResponse(description="Danh mục nguồn/kênh + ánh xạ")})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def list_channel_maps(request):
    """Danh mục nguồn/kênh Foxia kèm Channel nội bộ đã ánh xạ (nếu có)."""
    mapped = {
        (row.kind, row.foxia_id): row.channel_id
        for row in FoxiaChannelMap.objects.all()
    }
    sources = [
        {
            "kind": FoxiaChannelMapKind.SOURCE,
            "foxia_id": item.foxia_id,
            "name": item.name,
            "channel_id": mapped.get((FoxiaChannelMapKind.SOURCE, item.foxia_id)),
        }
        for item in FoxiaSource.objects.order_by("name")
    ]
    channels = [
        {
            "kind": FoxiaChannelMapKind.CHANNEL,
            "foxia_id": item.foxia_id,
            "name": item.name,
            "channel_id": mapped.get((FoxiaChannelMapKind.CHANNEL, item.foxia_id)),
        }
        for item in FoxiaChannel.objects.order_by("name")
    ]
    return Response({"sources": sources, "channels": channels})


@extend_schema(
    request=inline_serializer(
        "FoxiaChannelMapUpsert",
        fields={
            "kind": drf_serializers.CharField(),
            "foxia_id": drf_serializers.IntegerField(),
            "channel_id": drf_serializers.IntegerField(allow_null=True, required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "FoxiaChannelMapResult",
            fields={
                "kind": drf_serializers.CharField(),
                "foxia_id": drf_serializers.IntegerField(),
                "channel_id": drf_serializers.IntegerField(allow_null=True),
            },
        )
    },
)
@api_view(["PUT"])
@permission_classes([IsAuthenticated, require_permission(f"{SECTION}:edit")])
def upsert_channel_map(request):
    """Gán (hoặc gỡ) Channel nội bộ cho một nguồn/kênh Foxia.

    `channel_id = null` là GỠ ánh xạ — nguồn quay về nhóm "Chưa ánh xạ", đơn vẫn
    còn nguyên, chỉ mất phần chi phí.
    """
    kind = request.data.get("kind")
    foxia_id = request.data.get("foxia_id")
    channel_id = request.data.get("channel_id")

    if kind not in FoxiaChannelMapKind.values:
        return Response(
            {"detail": f"kind phải thuộc {FoxiaChannelMapKind.values}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        foxia_id = int(foxia_id)
    except (TypeError, ValueError):
        return Response({"detail": "foxia_id không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

    if channel_id in (None, "", 0):
        FoxiaChannelMap.objects.filter(kind=kind, foxia_id=foxia_id).delete()
        return Response({"kind": kind, "foxia_id": foxia_id, "channel_id": None})

    from apps.channels_core.models import Channel

    if not Channel.objects.filter(pk=channel_id, deleted_at__isnull=True).exists():
        return Response({"detail": "Kênh không tồn tại."}, status=status.HTTP_400_BAD_REQUEST)

    FoxiaChannelMap.objects.update_or_create(
        kind=kind, foxia_id=foxia_id, defaults={"channel_id": channel_id}
    )
    return Response({"kind": kind, "foxia_id": foxia_id, "channel_id": channel_id})


@extend_schema(responses={200: OpenApiResponse(description="Nhật ký đồng bộ gần nhất")})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def sync_status(request):
    """Sức khoẻ kết nối: trạng thái xác thực + 20 lượt đồng bộ gần nhất."""
    from apps.foxia.models import FoxiaConnection

    connection = FoxiaConnection.get_active()
    runs = FoxiaSyncRun.objects.order_by("-started_at")[:20]
    return Response(
        {
            "connection": (
                None
                if connection is None
                else {
                    "name": connection.name,
                    "database": connection.database,
                    "auth_status": connection.auth_status,
                    "auth_error": connection.auth_error,
                    "uid_checked_at": connection.uid_checked_at,
                }
            ),
            "runs": [
                {
                    "resource": run.resource,
                    "status": run.status,
                    "fetched": run.fetched,
                    "created": run.created,
                    "updated": run.updated,
                    "skipped": run.skipped,
                    "error_message": run.error_message,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                }
                for run in runs
            ],
        }
    )
