"""POST /api/contact-flow/customers/{id}/result và .../due — runtime tác nghiệp
(nút Chốt kết quả / Sửa giờ hẹn trên trang Danh sách contact, Sale).

Quyền `contacts:edit` (KHÔNG phải `contact_flow`) — đây là hành động GHI vận
hành trên trang contacts hiện có, không phải cấu hình flow (sweep N2 giai đoạn
2; grandfather 0039 cấp :edit cho role đang có contacts:view nên hành vi ngày
deploy không đổi). Scope dùng chung
`enforce_contacts_scope_or_404` từ
apps/orders/transport/views/order_views_impl/contact_scope.py (orders là chủ
domain contacts), KHÔNG viết lại logic scope. View mỏng: parse input → gọi
OperationEngine → map lỗi/payload.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.contact_flow.application.services.operation_engine import OperationEngine, OperationFlowError
from apps.contact_flow.transport.serializers.runtime_serializers import (
    ApplyResultRequestSerializer,
    EditDueRequestSerializer,
    RuntimeStateResponseSerializer,
    SetStageRequestSerializer,
    state_payload,
)
from apps.orders.transport.views.order_views_impl.contact_scope import enforce_contacts_scope_or_404
from apps.users.permissions import require_permission


@extend_schema(
    operation_id="contact_flow_apply_result",
    request=ApplyResultRequestSerializer,
    responses={
        200: RuntimeStateResponseSerializer,
        400: OpenApiResponse(description="Kết quả tác nghiệp không hợp lệ / thiếu thời gian hẹn"),
        404: OpenApiResponse(description="Không tìm thấy khách hàng hoặc ngoài phạm vi phụ trách"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("contacts:edit")])
def apply_result(request, customer_id: int):
    customer = enforce_contacts_scope_or_404(request.user, customer_id)

    payload = ApplyResultRequestSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    try:
        state = OperationEngine.apply_result(
            customer,
            payload.validated_data["result_id"],
            actor=request.user,
            scheduled_at=payload.validated_data.get("scheduled_at"),
        )
    except OperationFlowError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(state_payload(state))


@extend_schema(
    operation_id="contact_flow_edit_due",
    request=EditDueRequestSerializer,
    responses={
        200: RuntimeStateResponseSerializer,
        400: OpenApiResponse(description="Không cho phép sửa giờ hẹn ở bước hiện tại"),
        404: OpenApiResponse(description="Không tìm thấy khách hàng hoặc ngoài phạm vi phụ trách"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("contacts:edit")])
def edit_due(request, customer_id: int):
    customer = enforce_contacts_scope_or_404(request.user, customer_id)

    payload = EditDueRequestSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    try:
        state = OperationEngine.edit_due(
            customer, payload.validated_data["due_at"], actor=request.user,
        )
    except OperationFlowError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(state_payload(state))


@extend_schema(
    operation_id="contact_flow_set_stage",
    request=SetStageRequestSerializer,
    responses={
        200: RuntimeStateResponseSerializer,
        400: OpenApiResponse(description="Bước không tồn tại/đã tắt hoặc giờ hẹn không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy khách hàng hoặc ngoài phạm vi phụ trách"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("contacts:edit")])
def set_stage(request, customer_id: int):
    customer = enforce_contacts_scope_or_404(request.user, customer_id)

    payload = SetStageRequestSerializer(data=request.data)
    payload.is_valid(raise_exception=True)

    try:
        state = OperationEngine.set_stage(
            customer,
            payload.validated_data["stage_id"],
            payload.validated_data["due_at"],
            actor=request.user,
        )
    except OperationFlowError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(state_payload(state))
