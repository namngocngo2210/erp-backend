from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import PendingScanKind, PendingScanStatus, PendingShipmentScan
from apps.inventory.transport.serializers.pending_scan_serializers import (
    CreatePendingScansBatchSerializer,
    PendingShipmentScanSerializer,
)
from apps.users.permissions import require_permission, require_section
from apps.warehouses.models import Warehouse

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

_STATUS_CHOICES = set(PendingScanStatus.values)
_KIND_CHOICES = set(PendingScanKind.values)
_DEFAULT_STATUSES = [
    PendingScanStatus.PENDING,
    PendingScanStatus.FAILED,
    PendingScanStatus.INVALID,
    # Mã dừng vì đơn đã hủy: PHẢI hiện trên màn kho (kèm lý do ở `error`). Bỏ ra khỏi
    # danh sách = nuốt mã im lặng — cron không thử lại, kho không thấy dấu vết nào.
    PendingScanStatus.CANCELLED,
]


# GET: danh sách mã chờ khớp, mặc định chỉ mã CHƯA xử lý xong (pending/failed/invalid/
# cancelled — dùng cho màn kho); truyền status=matched để xem lịch sử.
@extend_schema(
    parameters=[
        OpenApiParameter(name="status", type=str, required=False, description="pending|matched|failed"),
        OpenApiParameter(name="kind", type=str, required=False, description="dispatch|return"),
    ],
    responses={
        200: inline_serializer(
            "PendingShipmentScanListResponse",
            fields={
                "items": PendingShipmentScanSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                "limit": drf_serializers.IntegerField(),
                "offset": drf_serializers.IntegerField(),
            },
        ),
        400: OpenApiResponse(description="Query param không hợp lệ"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("shipments")])
@never_cache
def list_pending_scans(request):
    pagination, error_response = parse_required_pagination(
        request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
    )
    if error_response is not None:
        return error_response

    qs = (
        PendingShipmentScan.objects.select_related(
            "warehouse", "scanned_by", "matched_order"
        )
        # Dòng đã THU HỒI (lần nhập hoàn tương ứng bị huỷ) biến mất khỏi MỌI danh
        # sách, không riêng tab Nhập hoàn — sót một đường đọc là dòng đã huỷ vẫn
        # hiện ở chỗ khác và người dùng lại tưởng đã nhập 2 lần.
        .filter(voided_at__isnull=True)
        .order_by("-created_at")
    )

    status_param = request.query_params.get("status")
    if status_param:
        if status_param not in _STATUS_CHOICES:
            return Response(
                {"detail": "Query param 'status' không hợp lệ."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = qs.filter(status=status_param)
    else:
        qs = qs.filter(status__in=_DEFAULT_STATUSES)

    kind_param = request.query_params.get("kind")
    if kind_param:
        if kind_param not in _KIND_CHOICES:
            return Response(
                {"detail": "Query param 'kind' không hợp lệ."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = qs.filter(kind=kind_param)

    total = qs.count()
    items = qs[pagination["offset"] : pagination["offset"] + pagination["limit"]]
    return Response(
        {
            "items": PendingShipmentScanSerializer(items, many=True).data,
            "total": total,
            "limit": pagination["limit"],
            "offset": pagination["offset"],
        },
        status=status.HTTP_200_OK,
    )


# POST: tạo hàng loạt mã chờ khớp từ 1 lượt quét.
@extend_schema(
    request=CreatePendingScansBatchSerializer,
    responses={
        201: inline_serializer(
            "PendingScanBatchResponse",
            fields={
                "results": inline_serializer(
                    "PendingScanBatchResult",
                    many=True,
                    fields={
                        "code": drf_serializers.CharField(),
                        "ok": drf_serializers.BooleanField(),
                        "reason": drf_serializers.CharField(required=False),
                    },
                ),
            },
        ),
        400: OpenApiResponse(description="Dữ liệu không hợp lệ hoặc không tìm thấy kho"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:edit")])
@never_cache
def create_pending_scans_batch(request):
    serializer = CreatePendingScansBatchSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    if not Warehouse.objects.filter(id=data["warehouse_id"]).exists():
        return Response(
            {"detail": "Không tìm thấy kho."}, status=status.HTTP_400_BAD_REQUEST
        )

    results = PendingScanService.create_batch(
        kind=data["kind"],
        warehouse_id=data["warehouse_id"],
        codes=list(data["codes"]),
        return_type=data.get("return_type", ""),
        user_id=request.user.id,
        scan_batch_id=data.get("scan_batch_id"),
    )
    return Response({"results": results}, status=status.HTTP_201_CREATED)


# POST: thử lại 1 dòng lỗi — reset về pending, xoá error/matched_order/result_ref.
@extend_schema(
    request=None,
    responses={
        200: PendingShipmentScanSerializer,
        400: OpenApiResponse(description="Chỉ dòng lỗi mới thử lại được"),
        404: OpenApiResponse(description="Không tìm thấy mã chờ khớp"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:edit")])
@never_cache
def retry_pending_scan(request, scan_id):
    try:
        scan = PendingShipmentScan.objects.select_related(
            "warehouse", "scanned_by", "matched_order"
        ).get(pk=scan_id)
    except PendingShipmentScan.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy mã chờ khớp."}, status=status.HTTP_404_NOT_FOUND
        )

    if scan.status != PendingScanStatus.FAILED:
        return Response(
            {"detail": "Chỉ dòng lỗi mới thử lại được."}, status=status.HTTP_400_BAD_REQUEST
        )

    scan.status = PendingScanStatus.PENDING
    scan.error = ""
    scan.matched_order = None
    scan.result_ref = ""
    scan.attempts = 0
    scan.save(
        update_fields=[
            "status", "error", "matched_order", "result_ref", "attempts", "updated_at",
        ]
    )
    return Response(PendingShipmentScanSerializer(scan).data, status=status.HTTP_200_OK)


# DELETE: xoá 1 dòng chờ khớp — chỉ khi chưa khớp (pending/failed).
@extend_schema(
    responses={
        204: OpenApiResponse(description="Đã xoá"),
        400: OpenApiResponse(description="Không xoá được dòng đã khớp"),
        404: OpenApiResponse(description="Không tìm thấy mã chờ khớp"),
    },
)
@api_view(["DELETE"])
@permission_classes([IsAuthenticated, require_permission("shipments:edit")])
@never_cache
def delete_pending_scan(request, scan_id):
    try:
        scan = PendingShipmentScan.objects.get(pk=scan_id)
    except PendingShipmentScan.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy mã chờ khớp."}, status=status.HTTP_404_NOT_FOUND
        )

    if scan.status == PendingScanStatus.MATCHED:
        return Response(
            {"detail": "Không xoá được dòng đã khớp đơn."}, status=status.HTTP_400_BAD_REQUEST
        )

    scan.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
