"""API trạng thái vận chuyển thực từ Pancake (two-way-sync §5.1, MR D) — chỉ đọc.

- `GET orders/<id>/delivery`: 2 field partner của đơn CRM + từng kiện Pancake (trang chi
  tiết đơn; quyền như `orders/<id>/refs`).
- `GET delivery/stuck`: đơn treo theo kho Pancake.
- `GET delivery/mismatches`: đơn lệch KiotViet ↔ Pancake (chỉ báo cáo, không sửa).
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.pancake_pos.application.services.factory import build_delivery_report_service
from apps.pancake_pos.models import InboundMatchStatus, PancakePosInboundOrder
from apps.pancake_pos.transport.delivery_serializers import (
    PancakePosDeliveryMismatchReportSerializer,
    PancakePosOrderDeliverySerializer,
    PancakePosStuckReportSerializer,
)
from apps.users.permissions import ORDER_SECTIONS, require_section

SECTION = "pancake-pos"

_PART_FIELDS = (
    "pancake_order_id", "pancake_status", "status_name", "dvvc_status", "tracking_code", "partner_name",
    "warehouse_name", "match_method", "inserted_at", "shipped_at", "delivered_at", "returned_at",
    "last_partner_status_at", "status_changed_at",
)


@extend_schema(operation_id="pancake_pos_order_delivery", responses={200: PancakePosOrderDeliverySerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION, *ORDER_SECTIONS)])
def order_delivery(request, order_id: int):
    """Trạng thái vận chuyển Pancake của một đơn CRM. Không có kiện → `parts` rỗng."""
    from apps.orders.models import Order

    partner = (
        Order.all_objects.filter(id=order_id)
        .values("partner_delivery_status", "partner_dvvc_delivery_status")
        .first()
    ) or {"partner_delivery_status": "", "partner_dvvc_delivery_status": ""}
    parts = list(
        PancakePosInboundOrder.objects.filter(order_id=order_id, match_status=InboundMatchStatus.MATCHED)
        .order_by("inserted_at", "id")
        .values(*_PART_FIELDS)
    )
    return Response({"order_id": order_id, **partner, "parts": parts})


@extend_schema(operation_id="pancake_pos_delivery_stuck", responses={200: PancakePosStuckReportSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def delivery_stuck(request):
    return Response(PancakePosStuckReportSerializer(build_delivery_report_service().stuck()).data)


@extend_schema(
    operation_id="pancake_pos_delivery_mismatches", responses={200: PancakePosDeliveryMismatchReportSerializer}
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def delivery_mismatches(request):
    return Response(PancakePosDeliveryMismatchReportSerializer(build_delivery_report_service().mismatches()).data)
