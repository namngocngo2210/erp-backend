"""View nghiệp vụ khách hàng thân thiết (danh sách, phân khúc, ghi chú chăm sóc)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from apps.users.permissions import can_assign_sale, require_permission, require_section

from apps.customers.customer_loyalty_service import CustomerLoyaltyService
from apps.customers.models import LoyalCustomerNote
from apps.customers.repositories import CustomerRepository
from apps.customers.serializers import (
    LoyalCustomerNoteUpdateSerializer,
    LoyalCustomerSerializer,
)

from ._view_helpers import _parse_required_pagination


@extend_schema(
    responses={
        200: inline_serializer(
            "LoyalCustomerListResponse",
            fields={
                "results": LoyalCustomerSerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("loyal-customers")])
def list_loyal_customers(request):
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    force_refresh = str(request.query_params.get("refresh", "")).strip().lower() in {"1", "true"}
    date_from = (request.query_params.get("date_from") or "").strip() or None
    date_to = (request.query_params.get("date_to") or "").strip() or None
    segment = (request.query_params.get("segment") or "").strip() or None
    category = (request.query_params.get("category") or "").strip() or None
    payload = CustomerLoyaltyService.list_loyal_customers(
        limit=pagination["limit"],
        offset=pagination["offset"],
        date_from=date_from,
        date_to=date_to,
        segment=segment,
        category=category,
        force_refresh=force_refresh,
        user=request.user,
    )
    serializer = LoyalCustomerSerializer(payload["results"], many=True)
    return Response({"results": serializer.data, "total": payload["total"]})


@extend_schema(
    responses={
        200: inline_serializer(
            "SegmentCategoryListResponse",
            many=True,
            fields={
                "code": drf_serializers.CharField(),
                "name": drf_serializers.CharField(),
                "criteria": drf_serializers.CharField(),
                "care_priority": drf_serializers.IntegerField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("loyal-customers")])
def list_segment_categories(request):
    return Response(CustomerLoyaltyService.list_segment_categories())


@extend_schema(
    request=LoyalCustomerNoteUpdateSerializer,
    responses={
        200: inline_serializer(
            "LoyalCustomerNoteUpdateResponse",
            fields={
                "sale_action": drf_serializers.CharField(allow_null=True),
                "special_note": drf_serializers.CharField(allow_null=True),
                "sale_staff_id": drf_serializers.IntegerField(allow_null=True),
                "sale_staff_name": drf_serializers.CharField(),
                "last_care_call_at": drf_serializers.CharField(allow_null=True),
            },
        ),
        404: OpenApiResponse(description="Không tìm thấy khách hàng"),
    },
)
@api_view(["PATCH"])
# Ghi note là hành vi SỬA, không phải xem. Gate cũ dùng require_section (= :view)
# nên ai xem được là ghi được toàn bộ ghi chú khách (audit 2026-08-07).
@permission_classes([IsAuthenticated, require_permission("loyal-customers:edit")])
def update_loyal_customer_note(request, customer_id: int):
    customer = CustomerRepository.get_customer_by_id(customer_id)
    if customer is None:
        return Response({"detail": "Không tìm thấy khách hàng."}, status=status.HTTP_404_NOT_FOUND)

    serializer = LoyalCustomerNoteUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    has_sale_staff = "sale_staff_id" in serializer.validated_data
    # FE gửi lại TOÀN BỘ form mỗi lần lưu (kể cả khi chỉ sửa special_note), nên
    # `sale_staff_id` luôn có mặt trong validated_data — presence check đơn thuần
    # chặn nhầm mọi lượt lưu của editor không có sale-assignment:edit. Phải so
    # với giá trị đang lưu: gửi lại đúng sale cũ (hoặc null khi chưa gán) không
    # phải là "đổi" và không được chặn oan (audit 2026-08-07, mirror order-PATCH gate).
    if has_sale_staff:
        existing_sale_id = (
            LoyalCustomerNote.objects.filter(customer=customer)
            .values_list("assigned_sale_id", flat=True)
            .first()
        )
        if (
            serializer.validated_data["sale_staff_id"] != existing_sale_id
            and not can_assign_sale(request.user)
        ):
            return Response(
                {"detail": "Bạn không có quyền gán sale phụ trách khách hàng."},
                status=status.HTTP_403_FORBIDDEN,
            )
    assigned_sale = None
    if has_sale_staff and serializer.validated_data["sale_staff_id"] is not None:
        assigned_sale = User.objects.get(id=serializer.validated_data["sale_staff_id"])
    has_last_care_call = "last_care_call_at" in serializer.validated_data
    note = CustomerLoyaltyService.upsert_loyal_note(
        customer,
        sale_action=serializer.validated_data.get("sale_action"),
        special_note=serializer.validated_data.get("special_note"),
        assigned_sale=assigned_sale,
        has_assigned_sale=has_sale_staff,
        last_care_call_at=serializer.validated_data.get("last_care_call_at"),
        has_last_care_call_at=has_last_care_call,
        updated_by=request.user if request.user.is_authenticated else None,
    )
    sale_staff_name = ""
    if note.assigned_sale_id and note.assigned_sale:
        sale_staff_name = note.assigned_sale.display_name or note.assigned_sale.username
    return Response(
        {
            "sale_action": note.sale_action,
            "special_note": note.special_note,
            "sale_staff_id": note.assigned_sale_id,
            "sale_staff_name": sale_staff_name,
            "last_care_call_at": note.last_care_call_at.isoformat() if note.last_care_call_at else None,
        },
        status=status.HTTP_200_OK,
    )
