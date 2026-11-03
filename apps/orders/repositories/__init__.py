"""Package repository đơn hàng — tách từ ``apps/orders/repositories.py`` (1404 dòng,
audit T3/P2-3) theo trách nhiệm, KHÔNG đổi hành vi:

- ``base.py``                — helper nền (base queryset, join CRM, KiotViet job
                               subquery, cache count) + hằng SEARCH_MATCH_ID_CAP /
                               EXPORT_MAX_ROWS.
- ``list_search.py``         — tìm theo từ khoá `q`.
- ``list_filter_attrs.py``   — bộ lọc thuộc tính (status/kênh/sản phẩm/NV/nguồn/...).
- ``list_filter_flags.py``   — bộ lọc cờ, quan hệ (Exists), assessment status, khoảng ngày.
- ``list_queries.py``        — ``apply_list_filters`` (ghép đúng thứ tự cũ) + list/đếm/lịch sử.
- ``export_queries.py``      — queryset export + scope kênh theo user/section.
- ``kiotviet_job_queries.py``— danh sách job KiotViet.

``OrderRepository`` vẫn là MỘT class (ghép mixin, cùng cách ``apps.orders.reporting``
đã làm); mọi đường import cũ ``from apps.orders.repositories import ...`` giữ nguyên
nhờ re-export dưới đây.
"""
from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.orders.reporting import (
    CANCELLED_ORDER_Q,
    KiotVietReportChannel,
    OrderReportsMixin,
    SALE_FB_CHANNEL_CODE,
    SALE_FB_CHANNEL_IDS,
    SalesReportBranch,
    SalesReportChannel,
)

from .base import EXPORT_MAX_ROWS, SEARCH_MATCH_ID_CAP, OrderQuerysetBaseMixin
from .export_queries import (
    OrderExportQueriesMixin,
    _apply_user_channel_scope,
    _section_filter_overrides,
    build_export_orders_queryset,
)
from .kiotviet_job_queries import OrderKiotVietJobQueriesMixin
from .list_queries import OrderListQueriesMixin

__all__ = [
    "EXCLUDED_BRANCH_IDS",  # re-export tương thích; nguồn chân lý: apps.common.business_constants
    "KiotVietReportChannel",
    "OrderRepository",
    "SALE_FB_CHANNEL_CODE",
    "SALE_FB_CHANNEL_IDS",
    "SalesReportBranch",
    "SalesReportChannel",
]

# Tên không nằm trong __all__ nhưng caller đang import trực tiếp (giữ tương thích):
# _apply_user_channel_scope (_view_helpers), _section_filter_overrides /
# build_export_orders_queryset / EXPORT_MAX_ROWS (exports_definitions),
# SEARCH_MATCH_ID_CAP, CANCELLED_ORDER_Q.
_COMPAT_EXPORTS = (
    CANCELLED_ORDER_Q,
    EXPORT_MAX_ROWS,
    SEARCH_MATCH_ID_CAP,
    _apply_user_channel_scope,
    _section_filter_overrides,
    build_export_orders_queryset,
)


class OrderRepository(
    OrderListQueriesMixin,
    OrderExportQueriesMixin,
    OrderKiotVietJobQueriesMixin,
    OrderQuerysetBaseMixin,
    OrderReportsMixin,
):
    """Repository đơn hàng — ghép các mixin truy vấn (package này) và mixin báo
    cáo (``apps.orders.reporting``). Không định nghĩa method riêng: mọi method
    sống ở module theo trách nhiệm, gọi nhau qua ``cls``."""
