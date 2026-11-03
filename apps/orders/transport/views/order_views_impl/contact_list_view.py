"""GET /api/sale/contacts — view mỏng bọc ContactListService (Sale contacts page).

Xem docs/sale-contacts-page-design.md. View chỉ parse query param → gọi service →
Response; datetime ISO-8601 hoá tự động qua JSONRenderer của DRF (rest_framework
serialize datetime/date bằng .isoformat() mặc định).
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.dates import parse_iso_date
from apps.common.pagination import lenient_limit, lenient_offset
from apps.orders.application.services.contact_list_service import ContactListService
from apps.orders.transport.serializers.contact_serializers import ContactListResponseSerializer
from apps.users.permissions import require_section

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _parse_date(raw: str | None):
    """Ngày hỏng → 400 với đúng thông báo mặc định của ``DateField`` DRF (như cũ)."""
    try:
        return parse_iso_date(raw, strict=True)
    except ValueError:
        message = drf_serializers.DateField.default_error_messages["invalid"]
        raise drf_serializers.ValidationError(message.format(format="YYYY-MM-DD"), code="invalid")


def _parse_int_list(raw_values: list[str]) -> list[int]:
    """getlist() cho query param multi giá trị (?channel=1&channel=2), mỗi item có
    thể còn chứa nhiều id ngăn cách bởi dấu phẩy (?channel=1,2) — chấp cả hai kiểu."""
    result: list[int] = []
    for raw in raw_values:
        for piece in str(raw).split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                result.append(int(piece))
            except ValueError:
                continue
    return result


def _parse_str_list(raw_values: list[str]) -> list[str]:
    result: list[str] = []
    for raw in raw_values:
        for piece in str(raw).split(","):
            piece = piece.strip()
            if piece:
                result.append(piece)
    return result


def _parse_limit(raw: str | None) -> int:
    return lenient_limit(raw, default=_DEFAULT_LIMIT, max_limit=_MAX_LIMIT)


def _parse_offset(raw: str | None) -> int:
    return lenient_offset(raw)


@extend_schema(
    operation_id="sale_contacts_list",
    parameters=[
        OpenApiParameter("date_from", str, description="YYYY-MM-DD"),
        OpenApiParameter("date_to", str, description="YYYY-MM-DD"),
        OpenApiParameter("q", str, description="Tìm theo tên/SĐT khách"),
        OpenApiParameter("channel", int, many=True, description="Lọc theo channel_id (nhiều giá trị)"),
        OpenApiParameter("order_source", str, many=True, description="Lọc theo order_source (nhiều giá trị)"),
        OpenApiParameter("staff", int, many=True, description="Lọc theo staff_id (nhiều giá trị)"),
        OpenApiParameter(
            "product", int, many=True, description="Lọc theo product_id (nhiều giá trị)"
        ),
        OpenApiParameter(
            "action_needed", str, many=True, description="Lọc theo action_needed (nhiều giá trị)"
        ),
        OpenApiParameter("interaction", str, description="'none' | 'recent'"),
        OpenApiParameter("customer_type", str, description="'new' | 'old'"),
        OpenApiParameter(
            "operation_bucket",
            str,
            description="Lọc theo bucket state tác nghiệp: 'overdue' | 'today' | 'upcoming' | 'ended' | 'none'",
        ),
        OpenApiParameter("limit", int, description=f"Mặc định {_DEFAULT_LIMIT}, tối đa {_MAX_LIMIT}"),
        OpenApiParameter("offset", int, description="Mặc định 0"),
    ],
    responses={200: ContactListResponseSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("contacts")])
def sale_contacts_list(request):
    params = request.query_params
    result = ContactListService.list_contacts(
        user=request.user,
        date_from=_parse_date(params.get("date_from")),
        date_to=_parse_date(params.get("date_to")),
        q=params.get("q", ""),
        channel_ids=_parse_int_list(params.getlist("channel")) or None,
        order_sources=_parse_str_list(params.getlist("order_source")) or None,
        staff_ids=_parse_int_list(params.getlist("staff")) or None,
        product_ids=_parse_int_list(params.getlist("product")) or None,
        action_needed=_parse_str_list(params.getlist("action_needed")) or None,
        interaction=params.get("interaction", ""),
        customer_type=params.get("customer_type", ""),
        operation_bucket=params.get("operation_bucket") or None,
        limit=_parse_limit(params.get("limit")),
        offset=_parse_offset(params.get("offset")),
    )
    return Response(result)
