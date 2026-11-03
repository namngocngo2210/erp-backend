from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.transport.views.order_views_impl._view_helpers import scoped_orders_queryset
from apps.users.permissions import require_permission
from apps.warehouses.models import Warehouse

# Decorator guard đúng một section "shipments" → record-scope cũng resolve đúng section
# đó (quy ước của `order_record_scope_q`: `sections` phải khớp `require_section`).
#
# "shipments" là TẬP CHA trong `_SECTION_ORDER_IDENTITY_Q` (xem comment ở đó): điều kiện
# hiển thị của trang Vận đơn là trạng thái vận hành biến đổi, không phải danh tính đơn.
# Nhờ vậy nhân viên kho scope ALL/CHANNEL vẫn xuất được đơn của mọi sale — nghiệp vụ kho
# không bị chặn oan — trong khi user scope OWN không còn xuất kho hộ đơn người khác.
_DISPATCH_SCOPE_SECTIONS = ("shipments",)

# Đơn ngoài record-scope trả CÙNG mã lỗi với đơn không tồn tại. Cố ý: mã riêng
# ("forbidden") sẽ biến endpoint thành oracle dò sự tồn tại của đơn qua vòng lặp id.
# Cùng quy ước với `bulk_delete_orders` (id ngoài scope im lặng như id không có thật).
_OUT_OF_SCOPE_ERROR = "not_found"


@extend_schema(
    request=inline_serializer(
        "DispatchBatchRequest",
        fields={
            "fulfillment_warehouse_id": drf_serializers.IntegerField(),
            "order_ids": drf_serializers.ListField(child=drf_serializers.IntegerField()),
        },
    ),
    responses={
        200: inline_serializer(
            "DispatchBatchResponse",
            # results: list kết quả từng đơn (hình dạng do OrderService quyết định).
            fields={"results": drf_serializers.ListField(child=drf_serializers.DictField())},
        ),
        400: OpenApiResponse(description="Thiếu/không hợp lệ warehouse hoặc order_ids"),
    },
)
# Xuất kho lô là hành vi GHI (trừ tồn + đổi trạng thái giao) → shipments:edit
# (sweep N2 giai đoạn 2; grandfather 0039 giữ hành vi ngày deploy).
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:edit")])
def dispatch_batch(request):
    warehouse_id = request.data.get("fulfillment_warehouse_id")
    order_ids = request.data.get("order_ids") or []
    if not warehouse_id:
        return Response({"detail": "fulfillment_warehouse_id is required."}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(order_ids, list) or not order_ids:
        return Response({"detail": "order_ids must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        warehouse_id = int(warehouse_id)
        coerced_order_ids = [int(x) for x in order_ids]
    except (ValueError, TypeError):
        return Response({"detail": "fulfillment_warehouse_id and each order_id must be integers."}, status=status.HTTP_400_BAD_REQUEST)
    if not Warehouse.objects.filter(id=warehouse_id).exists():
        return Response({"detail": f"Warehouse {warehouse_id} not found."}, status=status.HTTP_400_BAD_REQUEST)
    # Record-scope TRƯỚC khi chạm service: `dispatch_orders_batch` là điểm THẬT SỰ trừ
    # tồn + đổi trạng thái giao hàng, mà trước fix endpoint chỉ gate `require_section`.
    # `UserService.can_edit_order` fail-open cho scope OWN/ALL nên một user scope OWN chỉ
    # cần đoán id là xuất kho được đơn của bất kỳ ai (audit 2026-07-26 N1).
    allowed_ids = set(
        scoped_orders_queryset(request.user, _DISPATCH_SCOPE_SECTIONS)
        .filter(id__in=coerced_order_ids)
        .values_list("id", flat=True)
    )
    scoped_ids = [oid for oid in coerced_order_ids if oid in allowed_ids]
    # Vẫn trả một dòng kết quả cho id bị loại: FE (ScanDispatchDialog) map kết quả theo
    # `order_id`, nuốt mất dòng sẽ khiến kiện hàng đã quét hiện "đang chờ" mãi mãi.
    blocked = [
        {"order_id": oid, "ok": False, "error": _OUT_OF_SCOPE_ERROR}
        for oid in coerced_order_ids
        if oid not in allowed_ids
    ]
    results = (
        OrderService.dispatch_orders_batch(
            order_ids=scoped_ids,
            warehouse_id=warehouse_id,
            actor=request.user,
        )
        if scoped_ids
        else []
    )
    return Response({"results": [*results, *blocked]})
