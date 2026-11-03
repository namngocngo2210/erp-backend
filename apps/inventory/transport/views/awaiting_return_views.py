"""Endpoint tab 'Chờ hàng hoàn về' — đơn đã huỷ mà hàng còn ngoài kho.

Wire lớp transport trên `AwaitingReturnService`. Số lượng còn nợ do service đọc từ sổ;
view không tự tính gì.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.awaiting_return_service import (
    AwaitingReturnService,
)
from apps.inventory.flow_filters import parse_warehouse_ids
from apps.users.permissions import require_permission, require_section

_NOT_IN_QUEUE = "Đơn không nằm trong danh sách chờ hàng về."


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_AWAITING_RETURN_ITEM = {
    "order_id": drf.IntegerField(),
    "order_code": drf.CharField(),
    "invoice_code": drf.CharField(),
    "shipping_code": drf.CharField(),
    "customer_name": drf.CharField(),
    "warehouse_id": drf.IntegerField(),
    "warehouse_name": drf.CharField(),
    "cancelled_at": drf.DateTimeField(),
    "days_waiting": drf.IntegerField(),
    "status": drf.CharField(),
    "closed_reason": drf.CharField(),
    "closed_note": drf.CharField(),
    # Ghi chú tự do của dòng — RIÊNG của tab này (khác ghi chú cùng đơn ở tab Xuất).
    "note": drf.CharField(allow_blank=True),
    # SL đang nợ kho — luôn dương, đọc từ sổ (không phải số dư bút toán).
    "lines": inline_serializer(
        "AwaitingReturnLine",
        many=True,
        fields={
            "product_id": drf.IntegerField(),
            "sku": drf.CharField(),
            "qty": drf.IntegerField(),
        },
    ),
}


@extend_schema(
    tags=["inventory"],
    responses={
        200: inline_serializer(
            "AwaitingReturnPage",
            fields={
                "count": drf.IntegerField(),
                "results": inline_serializer(
                    "AwaitingReturnItem", many=True, fields=_AWAITING_RETURN_ITEM
                ),
            },
        )
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("returns")])
@never_cache
def awaiting_return_list(request):
    pagination, error = parse_required_pagination(request, default_limit=50)
    if error is not None:
        return error
    total, results = AwaitingReturnService.list_page(
        # Cùng luật đọc tham số kho với 2 tab kia (tab này dùng chung ô lọc kho).
        warehouse_ids=parse_warehouse_ids(
            request.query_params.get("warehouse_ids"),
            request.query_params.get("warehouse_id"),
        ),
        status=(request.query_params.get("status") or "waiting").strip(),
        min_days=_int_or_none(request.query_params.get("min_days")) or 0,
        limit=pagination["limit"],
        offset=pagination["offset"],
    )
    return Response({"count": total, "results": results})


@extend_schema(
    tags=["inventory"],
    request=inline_serializer(
        "AwaitingReturnCloseRequest",
        fields={
            "reason": drf.CharField(),
            "note": drf.CharField(required=False, allow_blank=True),
        },
    ),
    responses={
        200: inline_serializer(
            "AwaitingReturnCloseResponse", fields={"status": drf.CharField()}
        ),
        400: inline_serializer(
            "AwaitingReturnCloseError", fields={"detail": drf.CharField()}
        ),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("returns:approve")])
def awaiting_return_close(request, order_id: int):
    """Đóng sổ đơn treo. Không ghi bút toán kho — xem AwaitingReturnService.close."""
    try:
        AwaitingReturnService.close(
            order_id=order_id,
            reason=(request.data.get("reason") or "").strip(),
            note=request.data.get("note") or "",
            user_id=request.user.id,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if message == _NOT_IN_QUEUE
            else status.HTTP_400_BAD_REQUEST
        )
        return Response({"detail": message}, status=code)
    return Response({"status": "closed"})
