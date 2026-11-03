from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import ORDER_EDIT_CODES, require_permission

from apps.orders.assessment_fields import AssessmentValidationError, coerce_and_validate
from apps.orders.models import OrderAssessment, OrderJourneyEvent
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.sheet_rules import CASE_CLOSED_EVENT

from ._view_helpers import ORDER_WRITE_SCOPE_SECTIONS, scoped_order_or_error


@extend_schema(
    request=inline_serializer(
        "CloseCaseRequest",
        fields={"reason": drf_serializers.CharField(required=False, allow_null=True)},
    ),
    responses={
        200: inline_serializer(
            "CloseCaseResponse", fields={"ok": drf_serializers.BooleanField()}
        ),
        400: OpenApiResponse(description="Lý do không hợp lệ"),
        403: OpenApiResponse(description="Không có quyền thao tác đơn của kênh này"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_EDIT_CODES)])
def close_case(request, order_id: int):
    # Record-scope TRƯỚC mọi thứ: trước fix endpoint lấy đơn bằng `get_object_or_404`
    # rồi chỉ gate `can_edit_channel`, mà `can_edit_order` trả True VÔ ĐIỀU KIỆN cho
    # scope OWN → một sale scope OWN ĐÓNG (archive) được đơn của bất kỳ ai chỉ bằng
    # cách đổi `order_id` (audit 2026-07-26 N1). Giữ nguyên gate `can_edit_channel`
    # bên dưới — hàm này chỉ SIẾT thêm record scope.
    #
    # `sections` = ORDER_WRITE_SCOPE_SECTIONS vì decorator guard đúng `*ORDER_SECTIONS`.
    order, error = scoped_order_or_error(
        request, order_id, ORDER_WRITE_SCOPE_SECTIONS, select_related=("channel",)
    )
    if error is not None:
        return error
    if not OrderService.can_edit_channel(request.user, order.channel.code):
        return Response(
            {"detail": "Bạn không có quyền thao tác đơn của kênh này."},
            status=status.HTTP_403_FORBIDDEN,
        )
    reason = request.data.get("reason")
    try:
        if reason is not None:
            reason = coerce_and_validate("not_closed_reason_code", reason)
    except AssessmentValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    assessment, _ = OrderAssessment.objects.get_or_create(order=order)
    prev = assessment.sales_status_code
    assessment.sales_status_code = "archived"
    if reason is not None:
        assessment.not_closed_reason_code = reason
    assessment.updated_by = request.user
    assessment.save()
    OrderJourneyEvent.objects.create(
        order=order, event_type=CASE_CLOSED_EVENT, actor=request.user,
        from_code=prev, to_code="archived",
        payload_json={"reason": reason, "auto": False},
    )
    return Response({"ok": True})
