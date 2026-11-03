"""GET/POST /api/sale/contacts/{customer_id}/activities — timeline liên hệ +
ghi chú/log gọi (trang Danh sách contact, Sale).

Xem docs/sale-contacts-page-design.md §3.3. View mỏng: parse input → gọi
ContactActivityService → Response; datetime ISO-8601 hoá tự động qua JSONRenderer
của DRF (giống contact_list_view).
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.contact_activity_service import (
    ContactActivityService,
    ContactActivityValidationError,
)
from apps.orders.transport.serializers.contact_serializers import (
    ContactActivityCreateRequestSerializer,
    ContactActivityItemSerializer,
    ContactActivityListResponseSerializer,
)
from apps.orders.transport.views.order_views_impl.contact_list_view import _parse_limit, _parse_offset
from apps.orders.transport.views.order_views_impl.contact_scope import enforce_contacts_scope_or_404
from apps.users.permissions import require_method_permission

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


# Hợp đồng khai TÁCH THEO METHOD: GET trả envelope {items, total}, POST trả 201 +
# 1 item timeline (KHÔNG phải list) — gộp chung một extend_schema từng làm POST
# khai nhầm 200 list, FE phải workaround type tay.
@extend_schema(
    methods=["GET"],
    operation_id="sale_contact_activities",
    parameters=[
        OpenApiParameter("limit", int, description=f"Mặc định {_DEFAULT_LIMIT}, tối đa {_MAX_LIMIT}"),
        OpenApiParameter("offset", int, description="Mặc định 0"),
    ],
    responses={
        200: ContactActivityListResponseSerializer,
        404: OpenApiResponse(description="Không tìm thấy khách hàng hoặc ngoài phạm vi phụ trách"),
    },
)
@extend_schema(
    methods=["POST"],
    operation_id="sale_contact_activities_create",
    request=ContactActivityCreateRequestSerializer,
    responses={
        201: ContactActivityItemSerializer,
        400: OpenApiResponse(description="Payload không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy khách hàng hoặc ngoài phạm vi phụ trách"),
    },
)
# POST ghi timeline (note/log gọi) → contacts:edit; GET vẫn contacts:view.
# Grandfather 0039 cấp contacts:edit cho role đang có contacts:view — hành vi
# ngày deploy không đổi, siết thật = thu hồi per-role sau.
@api_view(["GET", "POST"])
@permission_classes([
    IsAuthenticated,
    require_method_permission(GET="contacts:view", POST="contacts:edit"),
])
def contact_activities(request, customer_id: int):
    customer = enforce_contacts_scope_or_404(request.user, customer_id)

    if request.method == "GET":
        params = request.query_params
        result = ContactActivityService.list_activities(
            customer,
            limit=_parse_limit(params.get("limit")),
            offset=_parse_offset(params.get("offset")),
        )
        return Response(result)

    try:
        item = ContactActivityService.create_activity(customer, request.user, request.data)
    except ContactActivityValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(item, status=status.HTTP_201_CREATED)
