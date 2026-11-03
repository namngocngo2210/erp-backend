"""View CRUD khách hàng (danh sách, tạo, sửa, xoá đơn lẻ và hàng loạt)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import ORDER_SECTIONS, require_method_permission, require_permission

# Audit 2026-08-19 N2: doc giu OR voi 3 trang don, GHI can quyen cung-action.
_CUST_SECTIONS = ("customers", *ORDER_SECTIONS)
_CUST_VIEW = tuple(f"{s}:view" for s in _CUST_SECTIONS)
_CUST_CREATE = tuple(f"{s}:create" for s in _CUST_SECTIONS)

from apps.customers.repositories import CustomerRepository
from apps.customers.serializers import (
    CustomerBulkDeleteSerializer,
    CustomerSerializer,
    CustomerUpsertSerializer,
)
from apps.customers.services import CustomerService, customer_phone_masker

from ._view_helpers import _parse_required_pagination


def _masker_context(user, customer_ids) -> dict:
    """Context che SĐT cho CustomerSerializer — 1 query ownership cho cả trang."""
    return {"phone_masker": customer_phone_masker(user, customer_ids)}


@extend_schema(
    request=CustomerUpsertSerializer,
    responses={
        200: CustomerSerializer(many=True),  # GET: danh sách khách hàng
        201: CustomerSerializer,  # POST: tạo khách hàng mới
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_CUST_VIEW, POST=_CUST_CREATE)])
def list_create_customers(request):
    if request.method == "POST":
        serializer = CustomerUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = CustomerService.create_customer(
            full_name=serializer.validated_data.get("full_name", ""),
            phone_raw=serializer.validated_data.get("phone_raw", ""),
            gender=serializer.validated_data.get("gender"),
        )
        return Response(
            CustomerSerializer(customer, context=_masker_context(request.user, [customer.id])).data,
            status=status.HTTP_201_CREATED,
        )

    q = request.query_params.get("q", "")
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    customers = list(
        CustomerRepository.list_customers(
            q,
            limit=pagination["limit"],
            offset=pagination["offset"],
        )
    )
    serializer = CustomerSerializer(
        customers,
        many=True,
        context=_masker_context(request.user, [c.id for c in customers]),
    )
    return Response(serializer.data)


@extend_schema(
    request=CustomerUpsertSerializer,
    responses={
        200: CustomerSerializer,  # PATCH: khách hàng sau cập nhật
        204: OpenApiResponse(description="DELETE thành công"),
        404: OpenApiResponse(description="Không tìm thấy khách hàng"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="customers:edit", DELETE="customers:delete")])
def update_delete_customer(request, customer_id: int):
    customer = CustomerRepository.get_customer_by_id(customer_id)
    if customer is None:
        return Response({"detail": "Không tìm thấy khách hàng."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        try:
            CustomerService.delete_customer(customer)
        except ValueError:
            return Response(
                {"detail": "Không thể xóa khách hàng đã có đơn hàng."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = CustomerUpsertSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    customer = CustomerService.update_customer(
        customer,
        full_name=serializer.validated_data.get("full_name"),
        phone_raw=serializer.validated_data.get("phone_raw"),
        gender=serializer.validated_data.get("gender"),
        has_gender="gender" in serializer.validated_data,
    )
    return Response(
        CustomerSerializer(customer, context=_masker_context(request.user, [customer.id])).data,
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=CustomerBulkDeleteSerializer,
    responses={
        200: inline_serializer(
            "CustomerBulkDeleteResponse",
            fields={
                "deleted_count": drf_serializers.IntegerField(),
                "protected_ids": drf_serializers.ListField(child=drf_serializers.IntegerField()),
            },
        ),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: ghi can quyen ghi, khong gate bang require_section (=:view).
@permission_classes([IsAuthenticated, require_permission("customers:delete")])
def bulk_delete_customers(request):
    serializer = CustomerBulkDeleteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    result = CustomerService.bulk_delete_customers(serializer.validated_data["ids"])
    return Response(result, status=status.HTTP_200_OK)
