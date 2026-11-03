"""Helper + mảnh serializer DÙNG CHUNG cho trang Lịch sử nhập xuất.

Xài bởi cả 3 file view của trang (`flow_issue_views` / `flow_return_views` /
`flow_pending_views`) và view tổng `flow_summary` (ở `flow_history_views`).

`_common_filters` Ở LẠI transport có chủ đích: nó đọc `request.query_params`
và trả thẳng DRF `Response` 400 khi ngày sai định dạng — là request parsing,
không phải logic thuần. Job export có nhánh parse RIÊNG
(`exports_definitions._common_filters`, đọc từ dict JSON thô) — hai nhánh phải
sửa song song, KHÔNG hợp nhất ở đây (xem chú thích bên exports_definitions).
"""
from __future__ import annotations

from datetime import datetime

from drf_spectacular.utils import inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.response import Response

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.flow_filters import int_csv, parse_warehouse_ids


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_date(value: str) -> bool:
    if not value:
        return True
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _common_filters(request):
    date_from = request.query_params.get("date_from") or ""
    date_to = request.query_params.get("date_to") or ""
    if not _valid_date(date_from) or not _valid_date(date_to):
        return None, Response(
            {"detail": "date_from/date_to phải dạng YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "warehouse_ids": parse_warehouse_ids(
            request.query_params.get("warehouse_ids"),
            request.query_params.get("warehouse_id"),
        ),
        "product": request.query_params.get("product") or "",
        "product_ids": int_csv(request.query_params.get("product_ids")),
        "order": request.query_params.get("order") or "",
        "carrier": (request.query_params.get("carrier") or "").strip(),
    }, None


# Dòng SKU rút gọn hiển thị ngay trong cell mã hóa đơn (dùng chung nhiều tab/khối).
_FLOW_LINE_BRIEF = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "qty": drf.IntegerField(),
    # SL đã TỪNG rời kho, chưa trừ phần hoàn/chuyển sổ. CHỈ có ở dòng tính theo
    # net (tab Xuất, mã chờ khớp) — dòng phiếu kho/chuyển kho/nhập hoàn mang
    # thẳng số dương nên `qty` đã đủ. FE dùng `gross` thay khi `qty` = 0.
    "gross": drf.IntegerField(required=False),
}
# Dùng chung 1 instance serializer cho mọi nơi (tab Xuất/Nhập hoàn + khối mã chờ
# khớp) — gọi inline_serializer riêng lẻ ở mỗi nơi sẽ tạo nhiều class khác nhau
# cùng tên "FlowLineBrief", khiến drf-spectacular cảnh báo trùng tên component.
_FLOW_LINE_BRIEF_SERIALIZER = inline_serializer(
    "FlowLineBrief", many=True, fields=_FLOW_LINE_BRIEF
)

_PENDING_ITEM = {
    "id": drf.IntegerField(),
    "code": drf.CharField(),
    "warehouse_name": drf.CharField(allow_null=True),
    "status": drf.CharField(),
    "status_label": drf.CharField(),
    # Người quét mã (scanned_by) — rỗng nếu bản ghi cũ không có user.
    "performed_by": drf.CharField(allow_blank=True),
    "error": drf.CharField(allow_blank=True),
    "created_at": drf.DateTimeField(),
    # Chỉ có giá trị với dòng đã khớp (status=matched).
    "matched_at": drf.DateTimeField(allow_null=True),
    "matched_order_code": drf.CharField(allow_blank=True),
    # Phiếu trả gắn với dòng (chỉ tab Nhập hoàn, dòng đã khớp) — để sửa ghi chú.
    "return_id": drf.IntegerField(allow_null=True),
    "note": drf.CharField(allow_blank=True),
    # SKU × SL của đơn/phiếu đã khớp (rỗng nếu chưa khớp) — cùng shape FlowLineBrief.
    "lines": _FLOW_LINE_BRIEF_SERIALIZER,
    "sku_count": drf.IntegerField(),
    "qty": drf.IntegerField(),
    # Nghi ngờ hàng gửi bù chưa có phiếu xuất kho (ca cũ) — chỉ đánh dấu, người quyết định.
    "needs_replacement_issue": drf.BooleanField(),
    # Dòng XUẤT đã khớp còn gỡ được (nút "Gỡ khớp") — luôn False với kind=return.
    "can_unmatch": drf.BooleanField(),
}


_MATCH_FILTERS = {"all", "matched", "unmatched"}


def _match_filter(request) -> str:
    """Bộ lọc trạng thái khớp cho danh sách mã quét; mặc định 'all'."""
    value = (request.query_params.get("match") or "all").strip()
    return value if value in _MATCH_FILTERS else "all"


def _pending_response(
    kind: str, filters: dict, match_filter: str, with_return_note: bool = False
):
    return Response(
        {
            "items": PendingScanService.list_unmatched(
                kind=kind,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
                warehouse_ids=filters["warehouse_ids"],
                match_filter=match_filter,
                with_return_note=with_return_note,
                # Khối "mã chờ khớp" dùng CHUNG ô tìm kiếm với danh sách lịch sử;
                # thiếu 2 tham số này thì gõ 1 mã vẫn hiện toàn bộ mã đã quét.
                order=filters.get("order", ""),
                product=filters.get("product", ""),
                product_ids=filters.get("product_ids"),
                carrier=filters.get("carrier", ""),
            )
        },
        status=status.HTTP_200_OK,
    )
