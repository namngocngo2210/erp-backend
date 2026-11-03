from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.analytics.infrastructure.mart_queries import (
    SEARCH_MIN_LENGTH,
    TIMELINE_MAX_ROWS,
    MartNotReady,
    coverage,
    customer_profile,
    customer_timeline,
    search_customers,
)
from apps.analytics.transport.serializers import (
    CoverageSerializer,
    CustomerProfileResponseSerializer,
    CustomerSearchHitSerializer,
)
from apps.users.permissions import require_section


@extend_schema(
    responses=CoverageSerializer,
    description="Độ phủ từng trường chân dung khách hàng, tách nguồn declared / ai.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def coverage_view(request):
    try:
        data = coverage()
    except MartNotReady as exc:
        return Response({"detail": str(exc)}, status=503)
    return Response(CoverageSerializer(data).data)


@extend_schema(
    parameters=[
        OpenApiParameter(
            "q",
            str,
            description=f"Chuỗi tìm theo mã khách, tên, hoặc SĐT. Tối thiểu {SEARCH_MIN_LENGTH} ký tự.",
        )
    ],
    responses={
        200: inline_serializer(
            "CustomerSearchResponse",
            fields={"results": CustomerSearchHitSerializer(many=True)},
        )
    },
    description="Tìm khách cho ô chọn của màn Hồ sơ 360.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("customer-explorer")])
def customer_search_view(request):
    """Chuỗi ngắn hơn ngưỡng trả về danh sách rỗng, KHÔNG phải 400.

    Ô tìm kiếm gõ tới đâu gọi tới đó, nên trạng thái "mới gõ 1 ký tự" là
    trạng thái bình thường của người dùng chứ không phải lỗi — trả 400 ở đây
    sẽ nhấp nháy thông báo lỗi trong lúc người ta đang gõ dở.
    """
    try:
        rows = search_customers(q=request.query_params.get("q", ""))
    except MartNotReady as exc:
        return Response({"detail": str(exc)}, status=503)
    return Response({"results": CustomerSearchHitSerializer(rows, many=True).data})


@extend_schema(
    responses={200: CustomerProfileResponseSerializer},
    description="Hồ sơ 360 của một khách: chân dung + dòng thời gian mua hàng.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("customer-explorer")])
def customer_profile_view(request, customer_id: int):
    try:
        profile = customer_profile(customer_id)
        if profile is None:
            return Response(
                {"detail": "Không tìm thấy hồ sơ cho khách này."},
                status=status.HTTP_404_NOT_FOUND,
            )
        timeline = customer_timeline(customer_id)
    except MartNotReady as exc:
        return Response({"detail": str(exc)}, status=503)

    payload = {
        "profile": profile,
        "timeline": timeline,
        # Cờ này để màn hình nói thật khi danh sách bị cắt, thay vì hiện 500 đơn
        # và để người xem tưởng đó là toàn bộ lịch sử.
        "timeline_truncated": len(timeline) >= TIMELINE_MAX_ROWS,
    }
    return Response(CustomerProfileResponseSerializer(payload).data)
