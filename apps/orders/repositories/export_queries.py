"""Queryset đơn cho export Excel + scope kênh theo user/section.

Tách nguyên trạng từ ``apps/orders/repositories.py`` (audit T3/P2-3) — KHÔNG đổi
hành vi, chỉ dời chỗ. Ba hàm module-level dưới đây vốn dời từ
apps/orders/transport/views/order_views_impl/order_export_view.py ở Task 18 (trả
nợ phân tầng: apps/orders/exports_definitions.py thuộc BUSINESS layer, trước đó
import 2 hàm này TỪ transport). Đặt cùng ``export_orders_queryset`` vì cả hai đều
là "queryset đơn cho export", không phải mối quan tâm của tầng transport.

``_apply_user_channel_scope`` là hàm thuần dict/user/section, không đụng
request/response nên đứng ở business layer — ``_view_helpers.py`` re-export lại
để các view còn dùng nó không phải đổi import.
"""
from django.db.models import Prefetch

from apps.orders.models import MARKETPLACE_ORDER_SOURCES, Order, OrderNeed


def _apply_user_channel_scope(
    filters: dict, user, section: str = "orders"
) -> tuple[dict, bool]:
    """Giao filters['channel'] với kênh user được xem; xử lý thêm scope 'own'.

    `section` phải khớp section mà endpoint guard bằng require_section (orders /
    koc-orders / landing-orders). Resolve scope theo đúng section đó — không hardcode
    "orders" — nếu không, role chỉ được cấp 1 section (vd "Phòng: Booking" chỉ có
    koc-orders) sẽ rơi vào scope mặc định OWN của section "orders" và chỉ thấy đơn
    do chính mình phụ trách thay vì toàn bộ theo scope thật của section.

    Returns (filters, should_return_empty).
    """
    from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

    scope_codes = resolved_channel_codes(user, section)
    if scope_codes is OWN_SCOPE_SENTINEL:
        return {**filters, "owner_user_id": user.id}, False
    if scope_codes is None:
        return filters, False
    if not scope_codes:
        return filters, True
    requested = filters.get("channel") or []
    if requested:
        intersection = [code for code in requested if code in scope_codes]
        if not intersection:
            return filters, True
        filters = {**filters, "channel": intersection}
    else:
        filters = {**filters, "channel": sorted(scope_codes)}
    return filters, False


def _section_filter_overrides(request, section: str) -> dict:
    """Khóa cứng tập đơn export-theo-filter theo trang gọi (`section`).

    Mirror đúng forced-filters của list view tương ứng (list_koc_orders /
    list_landing_orders) — các trang này KHÔNG dùng
    build_orders_filters thuần nên nếu chỉ build bộ lọc chung, file xuất sẽ chứa
    cả đơn ngoài trang (vd đơn thường lọt vào file KOC). Server tự quyết override
    theo section, client không nới được (quyền `{section}:export` đã check trước).
    Parity giữ bằng `OrdersExportSectionParityTests`/`BulkExportExcelSectionFilter
    Tests` cũ (test_orders_export_definition.py) — so tập export với tập list
    endpoint trả về trên cùng dữ liệu.

    `request` chỉ cần `.data` (dict-like) — caller là view thật (order_list_view
    không dùng hàm này) hoặc `_FiltersRequestShim` của `OrdersExport.validate`
    (exports_definitions.py, chạy async không có request thật).
    """
    source = request.data
    if section == "koc-orders":
        # list_koc_orders không default show_orders="true" như trang Đơn hàng.
        return {
            "show_orders": str(source.get("show_orders") or ""),
            "order_source": "koc",
        }
    if section == "landing-orders":
        overrides: dict = {
            # list_landing_orders không parse 2 key này.
            "kiotviet_channel_null": "",
            "show_orders": "",
            "order_source": "",
            "order_source_present": "true",
            "order_source_exclude": ["koc", *MARKETPLACE_ORDER_SOURCES],
        }
        # Phân Sheet 1/2 như list_landing_orders. Sheet 3 dùng endpoint riêng với
        # ràng buộc ngoài bộ lọc chung — FE không bật export-theo-filter cho tab đó.
        sheet = str(source.get("sheet") or "").strip()
        if sheet == "1":
            overrides["consulting"] = "exclude"
            overrides["order_source_exclude"] = [
                "koc", "kol", "messenger", *MARKETPLACE_ORDER_SOURCES,
            ]
        elif sheet == "2":
            overrides["consulting"] = "exclude"
            overrides["order_source"] = "messenger"
        return overrides
    return {}


def build_export_orders_queryset(user, section: str, ids, filters_source):
    """Queryset đơn cho một lượt xuất — dùng chung giữa view cũ (`bulk_export_excel`,
    đã xoá ở Task 18) và `OrdersExport` (apps/orders/exports_definitions.py).

    `filters_source` là dict bộ lọc ĐÃ PARSE (không phải request) để hàm này gọi
    được từ worker, nơi không có request object — caller tự dựng bằng
    `build_orders_filters(request)` (apps/orders/transport/views/order_views_impl/
    _view_helpers.py) + `_section_filter_overrides(request, section)` khi có
    request, hoặc dùng thẳng `params["filters"]` khi chạy từ ExportDefinition
    (worker đã nhận filters dạng dict qua job.params).

    Nhánh `ids`: áp scope bằng `_apply_user_channel_scope({}, ...)` (không có filter
    nào khác ngoài scope) — mirror đúng bulk_export_excel cũ, KHÔNG áp
    `_section_filter_overrides`/`filters_source` vì chọn theo id là chọn tường minh
    từng bản ghi, không phải theo trang.
    Nhánh filter: áp scope lên `filters_source` rồi giao cho
    `OrderRepository.export_orders_queryset` — bộ lọc mirror đúng đúng section gọi
    (đã được caller nhét `_section_filter_overrides` vào `filters_source`).
    """
    # Import cục bộ: OrderRepository được ghép ở package `__init__` từ chính mixin
    # của module này — import ở module level sẽ vòng.
    from apps.orders.repositories import OrderRepository

    scope_filters, scope_empty = _apply_user_channel_scope({}, user, section)
    if scope_empty:
        return Order.objects.none()
    if ids:
        orders = (
            # "recall"/"fulfillment_warehouse" thêm để order_row_values (export_excel.py)
            # đọc recall_code/return_slip_code/tên kho trực tiếp từ instance đã nạp sẵn —
            # thiếu 1-1 select_related này thì mỗi dòng (tới 50k) tự query lại (N+1).
            Order.objects.select_related(
                "channel", "kiotviet_channel", "customer", "staff", "recall", "fulfillment_warehouse"
            )
            .prefetch_related(
                "assessment",
                "recall_entries",
                Prefetch(
                    "assessment__needs",
                    queryset=OrderNeed.objects.select_related("need_type"),
                ),
            )
            .filter(id__in=ids, deleted_at__isnull=True)
            .order_by("-created_at", "-id")
        )
        if scope_filters.get("owner_user_id"):
            orders = orders.filter(staff_id=scope_filters["owner_user_id"])
        elif scope_filters.get("channel"):
            orders = orders.filter(channel__code__in=scope_filters["channel"])
        return orders

    filters, scope_empty = _apply_user_channel_scope(dict(filters_source), user, section)
    if scope_empty:
        return Order.objects.none()
    return OrderRepository.export_orders_queryset(filters)


class OrderExportQueriesMixin:
    @classmethod
    def export_orders_queryset(cls, filters: dict):
        """Full (unpaginated) queryset for Excel export, applying the SAME filters as
        the order list so an export reflects every matching order — not just the page
        the user is viewing. Carries the related data export_orders_to_excel reads
        (channel/staff/customer + recall + fulfillment_warehouse + assessment needs)
        to avoid N+1.
        """
        include_cancelled = cls._should_include_cancelled(filters)
        base = cls._apply_list_filters(
            cls._base_orders_queryset(include_cancelled=include_cancelled), filters
        )
        return (
            # "recall"/"fulfillment_warehouse" (1-1/FK): order_row_values đọc trực tiếp
            # từ instance đã nạp sẵn, tránh 1 query/dòng ở quy mô export tới 50k đơn.
            base.select_related(
                "channel", "kiotviet_channel", "customer", "staff", "recall", "fulfillment_warehouse"
            )
            .prefetch_related(
                "assessment",
                "recall_entries",
                Prefetch(
                    "assessment__needs",
                    queryset=OrderNeed.objects.select_related("need_type"),
                ),
            )
            .order_by("-created_at", "-id")
        )
