"""View theo dõi tái mua (resale tracking): danh sách, lần liên hệ, lịch hẹn, ghi chú."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.transport.views.order_views_impl._view_helpers import scoped_order_or_error
from apps.users.permissions import require_permission, require_section

from apps.customers.customer_loyalty_service import CustomerLoyaltyService
from apps.customers.customer_resale_service import CustomerResaleService
from apps.customers.repositories import CustomerRepository
from apps.customers.serializers import (
    CustomerResaleNoteUpdateSerializer,
    ResaleAppointmentUpdateSerializer,
    ResaleContactUpdateSerializer,
    ResaleOrderCreateSerializer,
    ResaleTrackingRowSerializer,
)

from ._view_helpers import _parse_required_pagination, _query_int

# Ba endpoint GHI của trang tái mua đều guard bằng `require_section("resale-tracking")`
# → record-scope resolve đúng section đó (quy ước của `order_record_scope_q`).
# "resale-tracking" được khai TƯỜNG MINH là tập CHA trong `_SECTION_ORDER_IDENTITY_Q`;
# lý do đầy đủ nằm cạnh khai báo đó.
_RESALE_SCOPE_SECTIONS = ("resale-tracking",)


def _resale_order_scope_error(request, order_id: int):
    """Guard authz cho các mutation tái mua khoá theo `order_id`.

    Trả Response (404/403) khi phải chặn, `None` khi được phép.

    Bản cũ (`_resale_channel_scope_error`) CHỈ hỏi `OrderService.can_edit_channel` →
    `UserService.can_edit_order`, mà hàm đó trả True VÔ ĐIỀU KIỆN cho scope OWN/ALL
    (gốc rễ audit 2026-07-26 N1): một sale scope OWN chỉ cần đổi `order_id` trên URL là
    tick liên hệ / đặt lịch hẹn / ghi đơn tái mua lên đơn của sale khác.

    Nay siết thêm record-scope TRƯỚC, và GIỮ NGUYÊN gate `can_edit_channel` phía sau:
    scope `department` mở kênh qua phòng ban còn `can_edit_channel` dựa trên UserChannel,
    hai lớp không thay thế được cho nhau.
    """
    order, error = scoped_order_or_error(
        request, order_id, _RESALE_SCOPE_SECTIONS, select_related=("channel",)
    )
    if error is not None:
        return error
    if not OrderService.can_edit_channel(request.user, order.channel.code):
        return Response(
            {"detail": "Bạn không có quyền thao tác đơn của kênh này."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


@extend_schema(
    responses={
        200: inline_serializer(
            "ResaleTrackingListResponse",
            fields={
                "results": ResaleTrackingRowSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                # facets = {sales: [{id, name}], segments: [{code, name}]}
                "facets": inline_serializer(
                    "ResaleTrackingFacets",
                    fields={
                        "sales": inline_serializer(
                            "ResaleTrackingFacetSale",
                            many=True,
                            fields={
                                "id": drf_serializers.IntegerField(),
                                "name": drf_serializers.CharField(),
                            },
                        ),
                        "segments": inline_serializer(
                            "ResaleTrackingFacetSegment",
                            many=True,
                            fields={
                                "code": drf_serializers.CharField(),
                                "name": drf_serializers.CharField(),
                            },
                        ),
                    },
                ),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("resale-tracking")])
def list_resale_tracking(request):
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response
    payload = CustomerResaleService.list_resale_tracking(
        limit=pagination["limit"],
        offset=pagination["offset"],
        q=request.query_params.get("q", ""),
        date_from=(request.query_params.get("date_from") or "").strip() or None,
        date_to=(request.query_params.get("date_to") or "").strip() or None,
        sale=_query_int(request, "sale"),
        contact_status=(request.query_params.get("contact_status") or "").strip() or None,
        segment=(request.query_params.get("segment") or "").strip() or None,
        min_days=_query_int(request, "min_days"),
        user=request.user,
    )
    serializer = ResaleTrackingRowSerializer(payload["results"], many=True)
    return Response(
        {"results": serializer.data, "total": payload["total"], "facets": payload["facets"]}
    )


@extend_schema(
    request=ResaleContactUpdateSerializer,
    responses={
        200: inline_serializer(
            "ResaleContactUpdateResponse",
            fields={
                "contact_1_at": drf_serializers.DateTimeField(allow_null=True),
                "contact_2_at": drf_serializers.DateTimeField(allow_null=True),
                "contact_3_at": drf_serializers.DateTimeField(allow_null=True),
                # contact_logs là JSON tự do (list các bản ghi liên hệ)
                "contact_logs": drf_serializers.JSONField(),
            },
        ),
        403: OpenApiResponse(description="Không có quyền thao tác kênh của đơn"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("resale-tracking:edit")])
def update_resale_contact(request, order_id: int):
    scope_error = _resale_order_scope_error(request, order_id)
    if scope_error is not None:
        return scope_error
    serializer = ResaleContactUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        followup = CustomerResaleService.update_resale_contact(
            order_id,
            serializer.validated_data["contact_no"],
            checked=serializer.validated_data["checked"],
            channel=serializer.validated_data.get("channel", ""),
            result=serializer.validated_data.get("result", ""),
            customer_status=serializer.validated_data.get("customer_status", ""),
            updated_by=request.user if request.user.is_authenticated else None,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return Response(
        {
            "contact_1_at": followup.contact_1_at,
            "contact_2_at": followup.contact_2_at,
            "contact_3_at": followup.contact_3_at,
            "contact_logs": followup.contact_logs,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=ResaleAppointmentUpdateSerializer,
    responses={
        200: inline_serializer(
            "ResaleAppointmentUpdateResponse",
            fields={
                "appointment": inline_serializer(
                    "ResaleAppointmentDetail",
                    fields={
                        "appointment_at": drf_serializers.DateTimeField(allow_null=True),
                        "channel": drf_serializers.CharField(),
                        "purpose": drf_serializers.CharField(),
                        "note": drf_serializers.CharField(),
                    },
                ),
            },
        ),
        403: OpenApiResponse(description="Không có quyền thao tác kênh của đơn"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("resale-tracking:edit")])
def update_resale_appointment(request, order_id: int):
    scope_error = _resale_order_scope_error(request, order_id)
    if scope_error is not None:
        return scope_error
    serializer = ResaleAppointmentUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        followup = CustomerResaleService.update_resale_appointment(
            order_id,
            appointment_at=serializer.validated_data["appointment_at"],
            channel=serializer.validated_data.get("channel", ""),
            purpose=serializer.validated_data.get("purpose", ""),
            note=serializer.validated_data.get("note", ""),
            updated_by=request.user if request.user.is_authenticated else None,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return Response(
        {
            "appointment": {
                "appointment_at": followup.appointment_at,
                "channel": followup.appointment_channel,
                "purpose": followup.appointment_purpose,
                "note": followup.appointment_note,
            }
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=ResaleOrderCreateSerializer,
    responses={
        201: inline_serializer(
            "ResaleOrderCreateResponse",
            fields={
                # resale_orders là JSON tự do (list ghi chú đơn resale)
                "resale_orders": drf_serializers.JSONField(),
            },
        ),
        403: OpenApiResponse(description="Không có quyền thao tác kênh của đơn"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: ghi can quyen ghi, khong gate bang require_section (=:view).
@permission_classes([IsAuthenticated, require_permission("resale-tracking:edit")])
def create_resale_order_note(request, order_id: int):
    scope_error = _resale_order_scope_error(request, order_id)
    if scope_error is not None:
        return scope_error
    serializer = ResaleOrderCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        followup = CustomerResaleService.create_resale_order_note(
            order_id,
            product=serializer.validated_data["product"],
            quantity=serializer.validated_data["quantity"],
            order_value=serializer.validated_data.get("order_value", 0),
            channel=serializer.validated_data.get("channel", ""),
            note=serializer.validated_data.get("note", ""),
            updated_by=request.user if request.user.is_authenticated else None,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return Response({"resale_orders": followup.resale_orders}, status=status.HTTP_201_CREATED)


@extend_schema(
    request=CustomerResaleNoteUpdateSerializer,
    responses={
        200: inline_serializer(
            "CustomerResaleNoteUpdateResponse",
            fields={
                "resale_status": drf_serializers.CharField(allow_null=True),
                "resale_suggestion": drf_serializers.CharField(allow_null=True),
            },
        ),
        404: OpenApiResponse(description="Không tìm thấy khách hàng"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("resale-tracking:edit")])
def update_customer_resale_note(request, customer_id: int):
    # NOTE (residual gap): keyed by customer, not a single channel. A customer's
    # orders can span multiple channels, so there is no single channel to scope
    # against here. The loyal-note record is per-customer (not per-order/channel),
    # so a per-channel object-level check is not cleanly feasible without changing
    # the data model. Access is still gated by require_section("resale-tracking").
    customer = CustomerRepository.get_customer_by_id(customer_id)
    if customer is None:
        return Response({"detail": "Không tìm thấy khách hàng."}, status=status.HTTP_404_NOT_FOUND)
    serializer = CustomerResaleNoteUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    note = CustomerLoyaltyService.upsert_loyal_note(
        customer,
        resale_status=serializer.validated_data.get("resale_status"),
        resale_suggestion=serializer.validated_data.get("resale_suggestion"),
        updated_by=request.user if request.user.is_authenticated else None,
    )
    return Response(
        {"resale_status": note.resale_status, "resale_suggestion": note.resale_suggestion},
        status=status.HTTP_200_OK,
    )
