"""Tab Nhập hoàn của trang Lịch sử nhập xuất — danh sách nhóm đơn hoàn,
drill-down SKU, sửa ghi chú nhóm và khối mã chờ khớp (kind=return) của tab.

Tách từ `flow_history_views.py` (refactor 2026-08); route/permission giữ
nguyên, urls.py vẫn import qua facade `flow_history_views`.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.transfer_document_history import (
    MergedReturnHistory,
)
from apps.inventory.models import PendingScanKind, ReturnEntry
from apps.inventory.transport.views.flow_history_shared import (
    _FLOW_LINE_BRIEF_SERIALIZER,
    _PENDING_ITEM,
    _common_filters,
    _int_or_none,
    _match_filter,
    _pending_response,
)
from apps.users.permissions import require_permission, require_section

_RETURN_ITEM = {
    # Dòng phiếu chuyển kho (kind="transfer") không phải nhóm đơn hoàn → return_id null.
    "kind": drf.ChoiceField(choices=["return", "transfer"]),
    "return_id": drf.IntegerField(allow_null=True),
    "code": drf.CharField(),
    "source_order_id": drf.IntegerField(allow_null=True),
    "source_order_code": drf.CharField(allow_null=True),
    "source_order_invoice_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    # Người tạo nhóm đơn hoàn (created_by) — rỗng nếu bản ghi cũ không có user.
    "performed_by": drf.CharField(allow_blank=True),
    "return_type": drf.CharField(allow_blank=True),
    "return_type_label": drf.CharField(),
    "qty_total": drf.IntegerField(),
    "note": drf.CharField(allow_blank=True),
    # Id phiếu chuyển kho của dòng kind="transfer" (null với nhóm đơn hoàn) —
    # FE cần để sửa ghi chú đúng nguồn.
    "doc_id": drf.IntegerField(allow_null=True),
    "created_at": drf.DateTimeField(),
    "lines": _FLOW_LINE_BRIEF_SERIALIZER,
}


@extend_schema(
    responses={
        200: inline_serializer(
            "ReturnHistory",
            fields={
                "items": inline_serializer(
                    "ReturnHistoryItem", many=True, fields=_RETURN_ITEM
                ),
                "total": drf.IntegerField(),
                # total_quantity = return + transfer (breakdown "hoàn A + chuyển kho B = C").
                "total_quantity": drf.IntegerField(),
                "return_quantity": drf.IntegerField(),
                "transfer_quantity": drf.IntegerField(),
                "limit": drf.IntegerField(),
                "offset": drf.IntegerField(),
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_history(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        MergedReturnHistory.list_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=inline_serializer(
        "ReturnHistoryNoteUpdate", fields={"note": drf.CharField(allow_blank=True)}
    ),
    responses={
        200: inline_serializer(
            "ReturnHistoryNoteResult",
            fields={"return_id": drf.IntegerField(), "note": drf.CharField(allow_blank=True)},
        ),
        404: OpenApiResponse(description="Không tìm thấy phiếu trả"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def return_note_update(request, return_id):
    """Sửa ghi chú của một nhóm đơn hoàn ngay trên trang Lịch sử nhập xuất.

    ``return_id`` là id NHÓM ĐƠN (ReturnEntry) — cùng giá trị mà danh sách và khối
    mã chờ khớp trả về; KHÔNG phải id phiếu ngày.
    """
    note = request.data.get("note", "")
    if not isinstance(note, str):
        return Response(
            {"detail": "note phải là chuỗi."}, status=status.HTTP_400_BAD_REQUEST
        )
    try:
        entry = ReturnEntryService.update_note(entry_id=return_id, note=note)
    except ReturnEntry.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu trả."}, status=status.HTTP_404_NOT_FOUND
        )
    return Response(
        {"return_id": entry.id, "note": entry.note}, status=status.HTTP_200_OK
    )


@extend_schema(
    responses={
        200: inline_serializer(
            "FlowPendingScansReturn",
            fields={
                "items": inline_serializer(
                    "FlowPendingScanReturnItem", many=True, fields=_PENDING_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_pending(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    return _pending_response(
        PendingScanKind.RETURN, filters, _match_filter(request), with_return_note=True
    )


_RETURN_SKU_ITEM = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "name": drf.CharField(allow_null=True),
    "return_count": drf.IntegerField(),
    "qty_total": drf.IntegerField(),
    "transfer_quantity": drf.IntegerField(),
    "transfer_count": drf.IntegerField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "ReturnSkusHistory",
            fields={
                "items": inline_serializer(
                    "ReturnSkusHistoryItem", many=True, fields=_RETURN_SKU_ITEM
                ),
                "total": drf.IntegerField(),
                "total_quantity": drf.IntegerField(),
                "return_quantity": drf.IntegerField(),
                "transfer_quantity": drf.IntegerField(),
                "limit": drf.IntegerField(),
                "offset": drf.IntegerField(),
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_skus(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        MergedReturnHistory.skus_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


_RETURN_SKU_RETURN_ITEM = {
    # Dòng phiếu chuyển kho (kind="transfer") không phải nhóm đơn hoàn → return_id null.
    "kind": drf.ChoiceField(choices=["return", "transfer"]),
    "return_id": drf.IntegerField(allow_null=True),
    "code": drf.CharField(),
    "source_order_code": drf.CharField(allow_null=True),
    "source_order_invoice_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    "return_type_label": drf.CharField(),
    "quantity": drf.IntegerField(),
    "created_at": drf.DateTimeField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "ReturnSkuReturns",
            fields={
                "items": inline_serializer(
                    "ReturnSkuReturnItem", many=True, fields=_RETURN_SKU_RETURN_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_sku_returns(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    product_id = _int_or_none(request.query_params.get("product_id"))
    if not product_id:
        return Response(
            {"detail": "product_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response(
        {"items": MergedReturnHistory.sku_drill(product_id=product_id, **filters)},
        status=status.HTTP_200_OK,
    )
