"""API webphone Zenify — cấp access token cho FE init SDK.

Gate theo quyền trang đơn landing (sale): ``landing-orders:view``. Toàn bộ
logic cache/single-flight nằm ở services.get_token — view chỉ là transport.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_section

from apps.callcenter import services
from apps.callcenter.models import CallRecord

SECTION = "landing-orders"
ORDERS_SECTION = "orders"


@extend_schema(
    operation_id="callcenter_webphone_token",
    request=None,
    parameters=[],
    responses={
        200: inline_serializer(
            name="WebphoneTokenResponse",
            fields={
                "token": drf_serializers.CharField(),
                "ttl": drf_serializers.IntegerField(),
                "email": drf_serializers.EmailField(),
                "agent_name": drf_serializers.CharField(allow_blank=True),
            },
        ),
        400: OpenApiResponse(description="User chưa có Email Zenify (zenify_email / email ERP đều rỗng)"),
        502: OpenApiResponse(description="Zenify trả lỗi (message nguyên văn)"),
        503: OpenApiResponse(description="Chưa cấu hình Zenify env"),
    },
    description="Cấp access token Zenify Web SDK cho agent hiện tại. ?refresh=1 để bỏ cache.",
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def webphone_token(request):
    if not services.zenify_configured():
        return Response(
            {"detail": "Zenify chưa được cấu hình (thiếu ZENIFY_DOMAIN/ZENIFY_API_KEY/ZENIFY_API_SECRET)."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    email = services.resolve_agent_email(request.user)
    if not email:
        return Response(
            {
                "detail": (
                    "Tài khoản của bạn chưa liên kết Email Zenify. "
                    "Nhờ admin nhập \"Email Zenify\" ở mục Người dùng → Sửa."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    refresh = str(request.query_params.get("refresh", "")).strip().lower() in {"1", "true"}
    try:
        payload = services.get_token(email, refresh=refresh)
    except services.ZenifyError as exc:
        return Response({"detail": str(exc)}, status=exc.status_code)
    return Response(payload)


def _can_view_order(user, order) -> bool:
    """Record-level scope 4 mức của section orders (bài học PII audit 2026-07-15b):
    transcript là PII — user chỉ thấy cuộc gọi của đơn mình được xem."""
    from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

    scope_codes = resolved_channel_codes(user, ORDERS_SECTION)
    if scope_codes is None:
        return True
    if scope_codes is OWN_SCOPE_SENTINEL:
        return order.staff_id == user.id
    return order.channel.code in scope_codes


@extend_schema(
    operation_id="callcenter_order_call_records",
    responses={
        200: inline_serializer(
            name="OrderCallRecordsResponse",
            fields={
                "items": drf_serializers.ListSerializer(
                    child=inline_serializer(
                        name="OrderCallRecordItem",
                        fields={
                            "id": drf_serializers.IntegerField(),
                            "call_type": drf_serializers.CharField(allow_blank=True),
                            "agent_name": drf_serializers.CharField(allow_blank=True),
                            "recording_url": drf_serializers.CharField(allow_blank=True),
                            "start_time": drf_serializers.DateTimeField(allow_null=True),
                            "end_time": drf_serializers.DateTimeField(allow_null=True),
                            "acd_time": drf_serializers.IntegerField(),
                            "transcribe_status": drf_serializers.IntegerField(),
                            "transcript": drf_serializers.CharField(allow_blank=True),
                        },
                    )
                ),
            },
        ),
        404: OpenApiResponse(description="Đơn không tồn tại hoặc ngoài scope của user"),
    },
    description="Danh sách cuộc gọi Zenify đã khớp với đơn (mới nhất trước).",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(ORDERS_SECTION)])
def order_call_records(request, order_id: int):
    from apps.orders.models import Order

    order = (
        Order.objects.filter(id=order_id, deleted_at__isnull=True)
        .select_related("channel")
        .first()
    )
    # 404 cho cả "không tồn tại" lẫn "ngoài scope" — không lộ sự tồn tại của đơn.
    if order is None or not _can_view_order(request.user, order):
        return Response({"detail": "Không tìm thấy đơn hàng."}, status=status.HTTP_404_NOT_FOUND)

    records = CallRecord.objects.filter(order=order).order_by("-start_time", "-id")
    items = [
        {
            "id": record.id,
            "call_type": record.call_type,
            "agent_name": record.agent_name,
            "recording_url": record.recording_url,
            "start_time": record.start_time,
            "end_time": record.end_time,
            "acd_time": record.acd_time,
            "transcribe_status": record.transcribe_status,
            "transcript": record.transcript,
        }
        for record in records
    ]
    return Response({"items": items})
