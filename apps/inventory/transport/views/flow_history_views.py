"""Trang Lịch sử nhập xuất — facade + view tổng đầu trang.

File gốc 939 dòng đã tách theo 3 nhóm màn hình (refactor 2026-08):
  • `flow_issue_views`   — tab Xuất giao (danh sách đơn, drill-down SKU, mã chờ khớp kind=dispatch)
  • `flow_return_views`  — tab Nhập hoàn (nhóm đơn hoàn, drill-down SKU, sửa note, mã chờ khớp kind=return)
  • `flow_pending_views` — thao tác trên mã chờ khớp (tra cứu đơn, khớp tay, phiếu bù, gỡ khớp, note/xoá)
  • `flow_history_shared` — helper + mảnh serializer dùng chung của trang

File này giữ lại `flow_summary` (số tổng CẢ TRANG, không thuộc riêng tab nào)
và re-export TƯỜNG MINH mọi view để `urls.py` giữ nguyên import — KHÔNG dùng
star-import. Route/permission không đổi.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.flow_summary_service import FlowSummaryService
from apps.inventory.transport.views.flow_history_shared import _common_filters
from apps.inventory.transport.views.flow_issue_views import (
    issue_orders,
    issue_pending,
    issue_sku_orders,
    issue_skus,
)
from apps.inventory.transport.views.flow_pending_views import (
    flow_manual_match,
    flow_order_lookup,
    flow_pending_delete,
    flow_pending_mark_invalid,
    flow_pending_note_update,
    flow_pending_replacement_issue,
    flow_pending_unmatch,
)
from apps.inventory.transport.views.flow_return_views import (
    return_history,
    return_note_update,
    return_pending,
    return_sku_returns,
    return_skus,
)
from apps.users.permissions import require_section

__all__ = [
    "flow_manual_match",
    "flow_order_lookup",
    "flow_pending_delete",
    "flow_pending_mark_invalid",
    "flow_pending_note_update",
    "flow_pending_replacement_issue",
    "flow_pending_unmatch",
    "flow_summary",
    "issue_orders",
    "issue_pending",
    "issue_sku_orders",
    "issue_skus",
    "return_history",
    "return_note_update",
    "return_pending",
    "return_sku_returns",
    "return_skus",
]


# ─────────── Số tổng hợp đầu trang + phân loại ĐVVC ───────────

_SUMMARY_CARRIER_ITEM = {
    "label": drf.CharField(),
    "order_count": drf.IntegerField(),
    "quantity": drf.IntegerField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "FlowSummary",
            fields={
                "issue": inline_serializer(
                    "FlowSummaryIssue",
                    fields={
                        "total_quantity": drf.IntegerField(),
                        "sale_quantity": drf.IntegerField(),
                        "doc_quantity": drf.IntegerField(),
                        "transfer_quantity": drf.IntegerField(),
                        "order_count": drf.IntegerField(),
                    },
                ),
                "return": inline_serializer(
                    "FlowSummaryReturn",
                    fields={
                        "total_quantity": drf.IntegerField(),
                        "return_quantity": drf.IntegerField(),
                        "transfer_quantity": drf.IntegerField(),
                        "return_count": drf.IntegerField(),
                    },
                ),
                "carriers": inline_serializer(
                    "FlowSummaryCarrier", many=True, fields=_SUMMARY_CARRIER_ITEM
                ),
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def flow_summary(request):
    """Số tổng (xuất/nhập hoàn, có breakdown) + số đơn/SL xuất theo ĐVVC — cùng
    bộ lọc với 2 tab để tile luôn khớp footer bảng."""
    filters, error = _common_filters(request)
    if error is not None:
        return error
    return Response(FlowSummaryService.summary(**filters), status=status.HTTP_200_OK)
