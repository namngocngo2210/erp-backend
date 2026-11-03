from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_limit

from apps.users.permissions import (
    ORDER_EDIT_CODES,
    ORDER_SECTIONS,
    require_permission,
    require_section,
)

from apps.orders.assessment_fields import AssessmentValidationError, coerce_and_validate
from apps.orders.crm_catalog import EVENT_TYPE_LABELS
from apps.orders.models import OrderAssessment, OrderJourneyEvent, OrderMilestoneSnapshot
from apps.orders.serializers import OrderJourneyEventSerializer
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.sheet_rules import ORDER_CREATED_EVENT, SALE_CONTACT_EVENT

from ._view_helpers import (
    INLINE_SCOPE_SECTIONS,
    ORDER_WRITE_SCOPE_SECTIONS,
    scoped_order_or_error,
)



# Trần dòng cho timeline/snapshot của MỘT đơn (audit 2026-09-23 L6).
_TIMELINE_DEFAULT_LIMIT = 200
_TIMELINE_MAX_LIMIT = 500

@extend_schema(
    request=inline_serializer(
        "ContactLogRequest",
        fields={
            "outcome": drf_serializers.CharField(required=False, allow_blank=True),
            "transcript": drf_serializers.CharField(required=False, allow_blank=True),
            "sales_status": drf_serializers.CharField(required=False, allow_null=True),
            "not_closed_reason": drf_serializers.CharField(required=False, allow_null=True),
        },
    ),
    responses={
        200: inline_serializer(
            "ContactLogResponse", fields={"ok": drf_serializers.BooleanField()}
        ),
        400: OpenApiResponse(description="Dữ liệu sales_status/not_closed_reason không hợp lệ"),
        403: OpenApiResponse(description="Không có quyền thao tác đơn của kênh này"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_EDIT_CODES)])
def create_contact_log(request, order_id: int):
    """Ghi 1 lần liên hệ → tạo OrderJourneyEvent mới (KHÔNG ghi đè).

    Body: { outcome: str, transcript?: str, sales_status?: str, not_closed_reason?: str }
    """
    # Record-scope TRƯỚC mọi thứ, cùng lý do với `close_case`: endpoint này ghi
    # `OrderJourneyEvent` VÀ `OrderAssessment.sales_status_code/not_closed_reason_code`
    # — đúng những cột close_case ghi. Trước fix chỉ có `get_object_or_404` +
    # `can_edit_channel`, mà `can_edit_order` trả True VÔ ĐIỀU KIỆN cho scope OWN/ALL
    # → sale scope OWN không có UserChannel nào vẫn đổi được sales_status và chèn
    # journey event giả mạo lên đơn bất kỳ chỉ bằng cách đổi `order_id`.
    # Giữ nguyên gate `can_edit_channel` bên dưới — chỉ SIẾT thêm record scope.
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
    outcome = str(request.data.get("outcome") or "").strip()
    transcript = str(request.data.get("transcript") or "").strip()
    sales_status = request.data.get("sales_status")
    not_closed_reason = request.data.get("not_closed_reason")

    try:
        if sales_status is not None:
            sales_status = coerce_and_validate("sales_status_code", sales_status)
        if not_closed_reason is not None:
            not_closed_reason = coerce_and_validate("not_closed_reason_code", not_closed_reason)
    except AssessmentValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    OrderJourneyEvent.objects.create(
        order=order,
        event_type=SALE_CONTACT_EVENT,
        actor=request.user,
        note=transcript,
        payload_json={"outcome": outcome, "transcript": transcript},
    )

    if sales_status is not None or not_closed_reason is not None:
        assessment, _ = OrderAssessment.objects.get_or_create(order=order)
        if sales_status is not None:
            assessment.sales_status_code = sales_status
        if not_closed_reason is not None:
            assessment.not_closed_reason_code = not_closed_reason
        assessment.updated_by = request.user
        assessment.save()

    return Response({"ok": True})


@extend_schema(
    responses={
        200: inline_serializer(
            "OrderJourneyEventsResponse",
            fields={"results": OrderJourneyEventSerializer(many=True)},
        ),
        403: OpenApiResponse(description="Không có quyền xem đơn của kênh này"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def get_order_journey_events(request, order_id: int):
    """Lịch sử hành trình (gồm các lần "Ghi liên hệ") của 1 đơn — load on-demand.

    Các list view (board Tư vấn) cố tình KHÔNG kèm journey events để query nhẹ,
    nên timeline được fetch riêng theo từng đơn khi user mở chi tiết.
    """
    # Record-scope THẬT thay cho gate cũ `order_channel_codes_scope`: hàm đó trả `None`
    # cho scope OWN (channel code không biểu diễn được "của tôi") nên điều kiện
    # `scope is not None and ...` luôn False → KHÔNG check gì. Body trả `note` +
    # `payload_json` chứa transcript cuộc gọi (PII), nên đây là read IDOR thật sự:
    # user scope OWN đọc được transcript đơn của bất kỳ ai.
    #
    # Dùng INLINE_SCOPE_SECTIONS (có "shipments") để mirror đúng gate đọc chi tiết đơn
    # `update_order_inline` GET — ai xem được đơn thì xem được timeline của đơn đó.
    order, error = scoped_order_or_error(request, order_id, INLINE_SCOPE_SECTIONS)
    if error is not None:
        return error

    # Trần dòng (audit 2026-09-23 L6): timeline một đơn hiếm khi quá vài chục sự
    # kiện; mới nhất trước nên cắt đuôi là cắt sự kiện cũ nhất. Response giữ `results`.
    limit, limit_error = parse_limit(request, default=_TIMELINE_DEFAULT_LIMIT, max_limit=_TIMELINE_MAX_LIMIT)
    if limit_error is not None:
        return limit_error
    events = list(order.journey_events.select_related("actor").order_by("-created_at")[:limit])
    results = OrderJourneyEventSerializer(events, many=True).data
    # Chỉ khi đã đọc tới dòng cũ nhất (không bị cắt bởi limit) — dòng "Tạo đơn" luôn
    # là dòng cuối timeline, và response không được vượt limit.
    reached_oldest = len(events) < limit
    if reached_oldest and not any(e.event_type == ORDER_CREATED_EVENT for e in events):
        results.append(_legacy_created_row(order))
    return Response({"results": results})


def _legacy_created_row(order) -> dict:
    """Dòng "Tạo đơn" suy từ ``Order.created_at`` cho đơn tạo trước khi có event
    ``order_created`` (2026-09-25) — người tạo không được lưu nên để trống, FE hiện
    "Không rõ" thay vì "Hệ thống". ``id=0``: không phải dòng DB thật."""
    return {
        "id": 0,
        "event_type": ORDER_CREATED_EVENT,
        "event_type_label": EVENT_TYPE_LABELS.get(ORDER_CREATED_EVENT),
        "from_code": None,
        "to_code": None,
        "note": order.product or "",
        "actor_name": None,
        "payload_json": {"source": "legacy", "order_source": order.order_source or ""},
        "created_at": order.created_at,
    }


class OrderMilestoneSnapshotSerializer(drf_serializers.ModelSerializer):
    # Tiền trả string như các báo cáo khác — giữ độ chính xác Decimal qua JSON.
    total_amount = drf_serializers.CharField()
    total_after_discount = drf_serializers.CharField()

    class Meta:
        model = OrderMilestoneSnapshot
        fields = [
            "milestone",
            "total_amount",
            "total_after_discount",
            "quantity",
            "sku",
            "amount_source",
            "captured_at",
        ]


@extend_schema(
    responses={
        200: inline_serializer(
            "OrderMilestoneSnapshotsResponse",
            fields={"results": OrderMilestoneSnapshotSerializer(many=True)},
        ),
        403: OpenApiResponse(description="Không có quyền xem đơn của kênh này"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def get_order_milestone_snapshots(request, order_id: int):
    """Snapshot mốc vòng đời của 1 đơn (created / invoiced) — dùng để đối soát.

    Cùng gate record-scope với ``get_order_journey_events``: ai xem được đơn thì
    xem được snapshot của đơn đó.
    """
    order, error = scoped_order_or_error(request, order_id, INLINE_SCOPE_SECTIONS)
    if error is not None:
        return error

    limit, limit_error = parse_limit(request, default=_TIMELINE_DEFAULT_LIMIT, max_limit=_TIMELINE_MAX_LIMIT)
    if limit_error is not None:
        return limit_error
    snapshots = order.milestone_snapshots.order_by("captured_at")[:limit]
    return Response({"results": OrderMilestoneSnapshotSerializer(snapshots, many=True).data})
