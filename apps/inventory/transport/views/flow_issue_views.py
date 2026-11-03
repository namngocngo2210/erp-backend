"""Tab Xuất giao của trang Lịch sử nhập xuất — danh sách theo đơn, drill-down
SKU và khối mã chờ khớp (kind=dispatch) của tab.

Tách từ `flow_history_views.py` (refactor 2026-08); route/permission giữ
nguyên, urls.py vẫn import qua facade `flow_history_views`.
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
from apps.inventory.application.services.issue_document_history import (
    MergedIssueHistory,
)
from apps.inventory.models import PendingScanKind
from apps.inventory.transport.views.flow_history_shared import (
    _FLOW_LINE_BRIEF_SERIALIZER,
    _PENDING_ITEM,
    _common_filters,
    _int_or_none,
    _match_filter,
    _pending_response,
)
from apps.users.permissions import require_section

_ISSUE_ORDER_ITEM = {
    # Dòng phiếu xuất kho (kind="document") không có đơn → order_id/mã đơn = null.
    "kind": drf.ChoiceField(choices=["order", "document", "transfer"]),
    "order_id": drf.IntegerField(allow_null=True),
    "order_code": drf.CharField(allow_null=True),
    "invoice_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    # Mã kho THỰC QUÉT khác mã vận đơn/hoá đơn của đơn (khớp tay kiện gửi lại
    # bằng vận đơn mới) — FE hiện cạnh `shipping_code`; rỗng ở ca thường.
    "scan_codes": drf.ListField(child=drf.CharField()),
    # Mã phiếu xuất kho (chỉ dòng kind="document").
    "doc_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    # Người tạo movement mới nhất của đơn — rỗng nếu movement không có user.
    "performed_by": drf.CharField(allow_blank=True),
    # Ghi chú tự do của dòng. Dòng đơn lưu ở `stock_flow_notes`, dòng phiếu
    # xuất/chuyển kho lấy thẳng `StockDocument.note` — FE sửa qua một endpoint
    # duy nhất, phân biệt bằng `doc_id` null hay không.
    "note": drf.CharField(allow_blank=True),
    "doc_id": drf.IntegerField(allow_null=True),
    "sku_count": drf.IntegerField(),
    "dispatched": drf.IntegerField(),
    "reversed": drf.IntegerField(),
    "net": drf.IntegerField(),
    "last_at": drf.DateTimeField(allow_null=True),
    "lines": _FLOW_LINE_BRIEF_SERIALIZER,
}


@extend_schema(
    responses={
        200: inline_serializer(
            "IssueOrdersHistory",
            fields={
                "items": inline_serializer(
                    "IssueOrdersHistoryItem", many=True, fields=_ISSUE_ORDER_ITEM
                ),
                "total": drf.IntegerField(),
                # total_quantity = sale + doc + transfer (breakdown để FE hiện
                # "theo đơn A + phiếu kho B + chuyển kho C = D").
                "total_quantity": drf.IntegerField(),
                "sale_quantity": drf.IntegerField(),
                "doc_quantity": drf.IntegerField(),
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
def issue_orders(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        MergedIssueHistory.orders_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


@extend_schema(
    responses={
        200: inline_serializer(
            "FlowPendingScans",
            fields={
                "items": inline_serializer(
                    "FlowPendingScanItem", many=True, fields=_PENDING_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_pending(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    return _pending_response(PendingScanKind.DISPATCH, filters, _match_filter(request))


_ISSUE_SKU_ITEM = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "name": drf.CharField(allow_null=True),
    "order_count": drf.IntegerField(),
    "dispatched": drf.IntegerField(),
    "reversed": drf.IntegerField(),
    # net = SL xuất THEO ĐƠN — SL hàng THẬT rời kho trong kỳ, cùng thước đo với
    # tile "Tổng SL xuất" (đảo sổ sau khi hàng đã đi không trừ ngược lại);
    # doc_quantity = SL xuất qua PHIẾU KHO. FE hiện tổng = net + doc_quantity,
    # kèm chú thích "(gồm N phiếu kho)" khi doc_count>0.
    "net": drf.IntegerField(),
    "doc_quantity": drf.IntegerField(),
    "doc_count": drf.IntegerField(),
    "transfer_quantity": drf.IntegerField(),
    "transfer_count": drf.IntegerField(),
    "last_at": drf.DateTimeField(allow_null=True),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "IssueSkusHistory",
            fields={
                "items": inline_serializer(
                    "IssueSkusHistoryItem", many=True, fields=_ISSUE_SKU_ITEM
                ),
                "total": drf.IntegerField(),
                "total_quantity": drf.IntegerField(),
                "sale_quantity": drf.IntegerField(),
                "doc_quantity": drf.IntegerField(),
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
def issue_skus(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        MergedIssueHistory.skus_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


_ISSUE_SKU_ORDER_ITEM = {
    # Dòng phiếu kho (kind="document") không có đơn → order_id/mã đơn = null.
    "kind": drf.ChoiceField(choices=["order", "document", "transfer"]),
    "order_id": drf.IntegerField(allow_null=True),
    "order_code": drf.CharField(allow_null=True),
    "invoice_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    "doc_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    # `dispatched` = SL đã từng rời kho; `net` đã trừ phần hoàn/chuyển sổ nên có
    # thể bằng 0 (đơn huỷ đã chuyển sổ sang đơn `.NN`).
    "dispatched": drf.IntegerField(),
    "net": drf.IntegerField(),
    "last_at": drf.DateTimeField(allow_null=True),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "IssueSkuOrders",
            fields={
                "items": inline_serializer(
                    "IssueSkuOrderItem", many=True, fields=_ISSUE_SKU_ORDER_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_sku_orders(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    product_id = _int_or_none(request.query_params.get("product_id"))
    if not product_id:
        return Response(
            {"detail": "product_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response(
        {"items": MergedIssueHistory.sku_drill(product_id=product_id, **filters)},
        status=status.HTTP_200_OK,
    )
