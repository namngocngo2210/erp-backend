"""View hồ sơ 360 độ của khách hàng: profile, đơn hàng và số liệu tổng hợp theo khách."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.sale_reminder_service import (
    get_next_reminder_at_map,
)
from apps.orders.repositories import OrderRepository
from apps.orders.serializers import OrderSerializer
from apps.users.permissions import ORDER_SECTIONS, require_section

from apps.customers.profile_service import CustomerProfileService
from apps.customers.repositories import CustomerRepository
from apps.customers.serializers import (
    CustomerOrderAggregateSerializer,
    CustomerProfileSerializer,
)
from apps.customers.services import CustomerService

from ._view_helpers import _parse_required_pagination


@extend_schema(
    responses={
        200: inline_serializer(
            "CustomerProfileListResponse",
            fields={
                "results": CustomerProfileSerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("customers", *ORDER_SECTIONS)])
def list_customer_profiles(request):
    q = request.query_params.get("q", "")
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response
    channel = (request.query_params.get("channel") or "").strip() or None
    sales_status = (request.query_params.get("sales_status") or "").strip() or None
    segment = (request.query_params.get("segment") or "").strip() or None
    payload = CustomerProfileService.list_profiles(
        q=q,
        limit=pagination["limit"],
        offset=pagination["offset"],
        channel=channel,
        sales_status=sales_status,
        segment=segment,
        user=request.user,
    )
    serializer = CustomerProfileSerializer(payload["results"], many=True)
    return Response({"results": serializer.data, "total": payload["total"]})


@extend_schema(
    responses={
        200: OrderSerializer(many=True),
        404: OpenApiResponse(description="Không tìm thấy khách hàng"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("customers", *ORDER_SECTIONS)])
def customer_orders(request, customer_id: int):
    customer = CustomerRepository.get_customer_by_id(customer_id)
    if customer is None:
        return Response({"detail": "Không tìm thấy khách hàng."}, status=status.HTTP_404_NOT_FOUND)

    # Import cục bộ (cùng khuôn `order_field_policy` vốn có ở đây): view của customers
    # gọi sang tầng transport của orders, để ở module-level dễ tạo vòng import.
    from apps.orders.transport.views.order_views_impl._view_helpers import (
        order_base_context,
        order_record_scope_q,
    )

    # Record-scope: `customer_id` là tham số THUẦN do client chọn, không ràng buộc gì
    # với đơn user được xem — trước fix chỉ cần một section bất kỳ trong danh sách
    # guard là đọc được TOÀN BỘ đơn (kèm PII khách + CRM) của khách bất kỳ, y hệt lỗ
    # hổng của `list_order_history` phía anh em `list_history_by_phone`
    # (audit 2026-07-26 N1, phía ĐỌC).
    #
    # Lọc ở TẦNG VIEW chứ không đổi chữ ký repository: `list_customer_orders` còn
    # call-site `CustomerService.build_customer_order_aggregate` — nơi siết scope sẽ
    # LÀM HỎNG nghiệp vụ (aggregate là cảnh báo liên kênh "khách này từng bom hàng của
    # người khác"; siết theo OWN thì luôn trả 0 đúng lúc cần cảnh báo nhất).
    #
    # `sections` phải khớp ĐÚNG danh sách của require_section ở trên: nếu bỏ "customers"
    # thì user CSKH chỉ được cấp section đó sẽ mất sạch danh sách đơn — chặn oan.
    scope_q = order_record_scope_q(request.user, ("customers", *ORDER_SECTIONS))
    # Cắt 200 đơn mới nhất (queryset đã order_by -created_at) ngay tại tầng view:
    # khách lâu năm có hàng nghìn đơn thì payload/serialize không có trần. Không đổi
    # repository — call-site khác của `list_customer_orders` cần đủ tập
    # (audit 2026-07-28 DB-7; phân trang thật xem docs/audit.md BE-3).
    orders = list(OrderRepository.list_customer_orders(customer_id).filter(scope_q)[:200])

    serializer = OrderSerializer(
        orders,
        many=True,
        # `order_base_context` gom field-policy (không rò field ẩn qua listing theo
        # khách hàng) + che SĐT đơn của sale khác.
        context=order_base_context(
            request.user,
            can_edit_fn=lambda _x: False,
            skip_journey_events=True,
            # Batch next-reminder cho cả trang (1 query) thay vì 1 query/đơn khi
            # serialize — cùng khuôn `_build_order_list_context` của list đơn chính
            # (audit 2026-07-28 DB-7).
            next_reminder_map=get_next_reminder_at_map(o.id for o in orders),
        ),
    )
    return Response(serializer.data)


@extend_schema(
    responses={
        200: CustomerOrderAggregateSerializer,
        404: OpenApiResponse(description="Không tìm thấy khách hàng"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("customers", *ORDER_SECTIONS)])
def customer_order_aggregate(request, customer_id: int):
    customer = CustomerRepository.get_customer_by_id(customer_id)
    if customer is None:
        return Response({"detail": "Không tìm thấy khách hàng."}, status=status.HTTP_404_NOT_FOUND)

    payload = CustomerService.build_customer_order_aggregate(customer)
    serializer = CustomerOrderAggregateSerializer(payload)
    return Response(serializer.data, status=status.HTTP_200_OK)
