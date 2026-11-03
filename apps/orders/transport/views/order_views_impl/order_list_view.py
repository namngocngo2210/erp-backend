import logging
import time

from django.db.models import Count, Q, prefetch_related_objects
from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.followup import annotate_contact_aggregates
from apps.orders.models import MARKETPLACE_ORDER_SOURCES
from apps.orders.repositories import OrderRepository
from apps.orders.serializers import OrderListSerializer
from apps.users.permissions import require_section

from ._view_helpers import (
    ORDERS_SORT_VALUES,
    _apply_user_channel_scope,
    _build_order_list_context,
    _empty_orders_response,
    _parse_multi_query_values,
    _parse_required_pagination,
    _parse_sort,
    apply_orders_sort,
    build_orders_filters,
)

logger = logging.getLogger(__name__)


def _sheet_stats_with_cache(filters: dict) -> dict[str, int]:
    """Stats toàn tập cho Sheet 1/2 ("mới chưa gọi" + "hẹn hôm nay") — trước đây
    aggregate quét toàn tập khớp bộ lọc chạy lại MỖI lần load sheet, mà sheet CSKH
    là list được refresh dày nhất giờ thường. Cache ngắn hạn cùng khuôn + cùng knob
    ORDERS_LIST_COUNT_CACHE_TTL với _count_with_cache/status_counts_for. Key theo
    bộ lọc bỏ sales_status (stats không phụ thuộc trục đó); "appointment_today"
    phụ thuộc ngày local nhưng TTL cỡ 30s nên không cần đưa ngày vào key (sai số
    tối đa TTL giây quanh nửa đêm).
    """
    from django.conf import settings
    from django.core.cache import cache

    ttl = int(getattr(settings, "ORDERS_LIST_COUNT_CACHE_TTL", 0) or 0)
    key_filters = {**filters, "sales_status": []}
    cache_key = None
    if ttl > 0:
        cache_key = OrderRepository._filters_cache_key(
            "orders:landing_sheet_stats:", key_filters
        )
        if cache_key is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

    stats_base = OrderRepository._apply_list_filters(
        OrderRepository._base_orders_queryset(include_cancelled=False), key_filters
    )
    # "Mới chưa gọi" = đơn chưa có trạng thái sale (assessment chưa được đánh giá,
    # hoặc sales_status_code null/rỗng).
    no_sales_status_q = (
        Q(assessment__isnull=True)
        | Q(assessment__sales_status_code__isnull=True)
        | Q(assessment__sales_status_code="")
    )
    stats_agg = stats_base.aggregate(
        uncalled=Count("id", filter=no_sales_status_q),
        appointment_today=Count(
            "id", filter=Q(assessment__next_followup_at__date=timezone.localdate())
        ),
    )
    result = {
        "uncalled": stats_agg["uncalled"] or 0,
        "appointment_today": stats_agg["appointment_today"] or 0,
    }
    if cache_key is not None:
        cache.set(cache_key, result, ttl)
    return result


def _run_orders_list(
    request,
    *,
    section: str,
    forced_filters: dict | None = None,
    with_status_counts: bool = True,
):
    """Ruột chung của list_orders / list_outside_orders — build filters, scope theo
    section, query, serialize. `section` phải khớp section mà endpoint guard bằng
    require_section, để _apply_user_channel_scope resolve đúng scope (xem docstring
    của _apply_user_channel_scope).

    `with_status_counts` chỉ bật cho endpoint có thanh chip lọc trạng thái ở FE
    (trang Đơn hàng); trang khác không hiển thị chip nên bỏ luôn aggregate này."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    sort_value = _parse_sort(request, default="newest")
    filters = {
        **build_orders_filters(request),
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }
    if forced_filters:
        filters.update(forced_filters)
    filters, scope_empty = _apply_user_channel_scope(filters, request.user, section)
    if scope_empty:
        return _empty_orders_response({"status_counts": {}} if with_status_counts else {})

    try:
        # list-timing (cùng họ landing-timing/create-timing): bão gate 2026-08-04
        # 09:13 toàn là POST /api/orders (list) nhưng view này không có số đo —
        # phải đoán khúc nào phình. Log để burst kế tiếp tự chỉ chỗ đau.
        _t0 = time.perf_counter()
        queryset, total_count, offset, limit = OrderRepository.list_orders(filters)
        _t1 = time.perf_counter()
        queryset = apply_orders_sort(queryset, sort_value)
        page = list(queryset[offset : offset + limit])
        _t2 = time.perf_counter()
        context = _build_order_list_context(request.user, page)
        _t3 = time.perf_counter()
        serializer = OrderListSerializer(page, many=True, context=context)
        data = {
            "results": serializer.data,
            "count": total_count,
        }
        _t4 = time.perf_counter()
        if with_status_counts:
            data["status_counts"] = OrderRepository.status_counts_for(filters)
        _t5 = time.perf_counter()
        logger.info(
            "list-timing section=%s rows=%d count=%.0fms page=%.0fms ctx=%.0fms "
            "serialize=%.0fms status_counts=%.0fms total=%.0fms",
            section,
            len(page),
            (_t1 - _t0) * 1000,
            (_t2 - _t1) * 1000,
            (_t3 - _t2) * 1000,
            (_t4 - _t3) * 1000,
            (_t5 - _t4) * 1000,
            (_t5 - _t0) * 1000,
        )
    except Exception:
        logger.exception("Failed to query/serialize orders list, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách đơn hàng. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(data)


# Hợp đồng OpenAPI (D7) cho endpoint danh sách đơn — mẫu vertical-slice để FE
# sinh type (S2). Request là body filter (POST); chỉ khai báo các field phổ biến,
# bổ sung dần. Response là wrapper phân trang {results, count}.
@extend_schema(
    operation_id="orders_list",
    request=inline_serializer(
        "OrderListRequest",
        fields={
            "limit": drf_serializers.IntegerField(required=False),
            "offset": drf_serializers.IntegerField(required=False),
            "sort": drf_serializers.ChoiceField(
                choices=ORDERS_SORT_VALUES, required=False
            ),
            "search": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "OrderListResponse",
            fields={
                "results": OrderListSerializer(many=True),
                "count": drf_serializers.IntegerField(),
                "status_counts": drf_serializers.DictField(
                    child=drf_serializers.IntegerField()
                ),
            },
        ),
        500: OpenApiResponse(description="Lỗi truy vấn/serialize danh sách đơn"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("orders")])
@never_cache
def list_orders(request):
    return _run_orders_list(request, section="orders")


@extend_schema(
    operation_id="orders_koc_list",
    request=inline_serializer(
        "KocOrderListRequest",
        fields={
            "limit": drf_serializers.IntegerField(required=False),
            "offset": drf_serializers.IntegerField(required=False),
            "sort": drf_serializers.ChoiceField(
                choices=ORDERS_SORT_VALUES, required=False
            ),
            "q": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "KocOrderListResponse",
            fields={
                "results": OrderListSerializer(many=True),
                "count": drf_serializers.IntegerField(),
                "status_counts": drf_serializers.DictField(
                    child=drf_serializers.IntegerField()
                ),
            },
        ),
        500: OpenApiResponse(description="Lỗi truy vấn/serialize danh sách đơn KOC"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("koc-orders")])
@never_cache
def list_koc_orders(request):
    """Danh sách đơn KOC — luôn khóa ở các đơn có order_source="koc"."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    source = request.data
    sort_value = _parse_sort(request, default="newest")
    filters = {
        "q": source.get("q", ""),
        "status": _parse_multi_query_values(request, "status"),
        "channel": _parse_multi_query_values(request, "channel"),
        "channel_type": _parse_multi_query_values(request, "channel_type"),
        "product": _parse_multi_query_values(request, "product"),
        "staff": _parse_multi_query_values(request, "staff"),
        "delivery_business_status": _parse_multi_query_values(request, "delivery_business_status"),
        "sales_status": _parse_multi_query_values(request, "sales_status"),
        "kiotviet_channel": _parse_multi_query_values(request, "kiotviet_channel"),
        "branch_id": _parse_multi_query_values(request, "branch_id"),
        "fulfillment_warehouse": _parse_multi_query_values(request, "fulfillment_warehouse"),
        "invoice_status": _parse_multi_query_values(request, "invoice_status"),
        "vat": _parse_multi_query_values(request, "vat"),
        "order_type": _parse_multi_query_values(request, "order_type"),
        # Trước đây thiếu key này: FE chỉ hiện ô lọc koc_order_type khi isKoc, mà khi
        # isKoc thì use-orders-table.ts lại gọi thẳng /api/orders/koc — nên chọn nhãn
        # KOC trên đúng trang KOC không lọc được gì (không lỗi, không log) dù export
        # Excel (dùng build_orders_filters) vẫn lọc đúng.
        "koc_order_type": _parse_multi_query_values(request, "koc_order_type"),
        "has_shipping_code": source.get("has_shipping_code", ""),
        "has_invoice_code": source.get("has_invoice_code", ""),
        "is_delivery_invoice_printed": source.get("is_delivery_invoice_printed", ""),
        "customer_return": source.get("customer_return", ""),
        "has_internal_note": source.get("has_internal_note", ""),
        "has_return_slip": source.get("has_return_slip", ""),
        "has_recall_code": source.get("has_recall_code", ""),
        "has_call_recording": source.get("has_call_recording", ""),
        "has_address": source.get("has_address", ""),
        "kiotviet_channel_null": source.get("kiotviet_channel_null", ""),
        "show_orders": source.get("show_orders", ""),
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        # Bộ lọc "Ngày hoàn hàng" (OrdersFilterPanel dùng chung cho cả KOC) — phải
        # đọc & áp như list_orders, nếu thiếu thì FE gửi return_date_from/to mà
        # list không lọc.
        "return_date_from": source.get("return_date_from", ""),
        "return_date_to": source.get("return_date_to", ""),
        # "Ngày tạo trên KiotViet" — filter parity với /api/orders.
        "kiotviet_purchase_date_from": source.get("kiotviet_purchase_date_from", ""),
        "kiotviet_purchase_date_to": source.get("kiotviet_purchase_date_to", ""),
        # Khóa cứng đơn KOC theo order_source, không phụ thuộc client gửi gì.
        "order_source": "koc",
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }
    filters, scope_empty = _apply_user_channel_scope(filters, request.user, "koc-orders")
    if scope_empty:
        return _empty_orders_response({"status_counts": {}})

    try:
        queryset, total_count, offset, limit = OrderRepository.list_orders(filters)
        queryset = apply_orders_sort(queryset, sort_value)
        page = list(queryset[offset : offset + limit])
        context = _build_order_list_context(request.user, page)
        serializer = OrderListSerializer(page, many=True, context=context)
        data = {
            "results": serializer.data,
            "count": total_count,
            "status_counts": OrderRepository.status_counts_for(filters),
        }
    except Exception:
        logger.exception("Failed to query/serialize KOC orders list, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách đơn KOC. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(data)


# Wrapper phân trang + sales_status_counts (đếm badge theo trạng thái sale);
# sheet_stats chỉ xuất hiện khi yêu cầu Sheet 1/2 (bổ sung, không bắt buộc).
@extend_schema(
    operation_id="orders_landing_list",
    request=inline_serializer(
        "LandingOrderListRequest",
        fields={
            "limit": drf_serializers.IntegerField(required=False),
            "offset": drf_serializers.IntegerField(required=False),
            "sort": drf_serializers.ChoiceField(
                choices=ORDERS_SORT_VALUES, required=False
            ),
            "q": drf_serializers.CharField(required=False),
            "sheet": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "LandingOrderListResponse",
            fields={
                "results": OrderListSerializer(many=True),
                "count": drf_serializers.IntegerField(),
                "sales_status_counts": drf_serializers.DictField(
                    child=drf_serializers.IntegerField()
                ),
            },
        ),
        500: OpenApiResponse(description="Lỗi truy vấn/serialize danh sách đơn Landing"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("landing-orders")])
@never_cache
def list_landing_orders(request):
    """Danh sách đơn Landing — chỉ hiển thị đơn có order_source (khác null) và loại đơn koc."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    source = request.data
    sort_value = _parse_sort(request, default="oldest")
    status_values = _parse_multi_query_values(request, "status")
    invoice_status_values = _parse_multi_query_values(request, "invoice_status")
    # Trang Landing gom mọi đơn có nguồn (landing/zalo/messenger/hotline...) nhưng loại đơn koc,
    # áp dụng cho mọi tab kể cả "Đã hủy" (chỉ đơn hủy có nguồn, trừ koc).
    filters = {
        "q": source.get("q", ""),
        "status": status_values,
        "channel": _parse_multi_query_values(request, "channel"),
        "channel_type": _parse_multi_query_values(request, "channel_type"),
        "product": _parse_multi_query_values(request, "product"),
        "staff": _parse_multi_query_values(request, "staff"),
        "sales_status": _parse_multi_query_values(request, "sales_status"),
        "delivery_business_status": _parse_multi_query_values(request, "delivery_business_status"),
        "kiotviet_channel": _parse_multi_query_values(request, "kiotviet_channel"),
        "branch_id": _parse_multi_query_values(request, "branch_id"),
        "fulfillment_warehouse": _parse_multi_query_values(request, "fulfillment_warehouse"),
        "invoice_status": invoice_status_values,
        # Bộ lọc "Xuất VAT" hiển thị trên trang Landing (OrdersFilterPanel) — phải
        # parse & áp như list_orders/list_koc_orders, nếu thiếu thì chọn vat sẽ
        # không lọc được trên Landing (đa số đơn company/individual là đơn có nguồn).
        "vat": _parse_multi_query_values(request, "vat"),
        "order_type": _parse_multi_query_values(request, "order_type"),
        "has_shipping_code": source.get("has_shipping_code", ""),
        "has_invoice_code": source.get("has_invoice_code", ""),
        "is_delivery_invoice_printed": source.get("is_delivery_invoice_printed", ""),
        "customer_return": source.get("customer_return", ""),
        "has_internal_note": source.get("has_internal_note", ""),
        "has_return_slip": source.get("has_return_slip", ""),
        "has_recall_code": source.get("has_recall_code", ""),
        "has_call_recording": source.get("has_call_recording", ""),
        "has_address": source.get("has_address", ""),
        "order_source": "",
        "order_source_present": "true",
        # Loại koc (có trang riêng) + đơn sàn TMĐT (không thuộc luồng sale Landing).
        "order_source_exclude": ["koc", *MARKETPLACE_ORDER_SOURCES],
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        # Bộ lọc "Ngày hoàn hàng" hiển thị trên trang Landing (OrdersFilterPanel) —
        # phải đọc & áp như list_orders, nếu thiếu thì FE gửi return_date_from/to mà
        # list không lọc.
        "return_date_from": source.get("return_date_from", ""),
        "return_date_to": source.get("return_date_to", ""),
        # "Ngày tạo trên KiotViet" — filter parity với /api/orders.
        "kiotviet_purchase_date_from": source.get("kiotviet_purchase_date_from", ""),
        "kiotviet_purchase_date_to": source.get("kiotviet_purchase_date_to", ""),
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }
    # Phân Sheet 1/2 (trang Landing): sheet=1 = Landing+Hotline+Zalo; sheet=2 =
    # Messenger. Cả hai LOẠI đơn thuộc board Tư vấn (thiếu thông tin hoặc đổ về
    # không có địa chỉ) — xem sheet_rules.consulting_q. Không truyền sheet =
    # hành vi cũ (tab Tổng hợp: mọi nguồn trừ koc).
    sheet = str(source.get("sheet") or "").strip()
    if sheet == "1":
        filters["consulting"] = "exclude"
        filters["order_source_exclude"] = ["koc", "kol", "messenger", *MARKETPLACE_ORDER_SOURCES]
    elif sheet == "2":
        filters["consulting"] = "exclude"
        filters["order_source"] = "messenger"
    filters, scope_empty = _apply_user_channel_scope(
        filters, request.user, "landing-orders"
    )
    if scope_empty:
        return _empty_orders_response({"sales_status_counts": {}})

    try:
        _t0 = time.perf_counter()
        # Badge counts grouped by sales_status_code use same filters EXCEPT sales_status,
        # so each badge reflects how many orders match if that status is selected.
        queryset, total_count, offset, limit, sales_status_counts = (
            OrderRepository.list_orders_with_status_counts(filters)
        )
        _t1 = time.perf_counter()
        queryset = apply_orders_sort(queryset, sort_value, default="oldest")
        is_sheet = sheet in ("1", "2")
        if is_sheet:
            # Đính kèm số lần gọi để FE tính SLA "mới chưa gọi" + highlight đỏ.
            queryset = annotate_contact_aggregates(queryset)
        # Fetch page WITHOUT triggering prefetch_related (run prefetch manually below).
        sliced = queryset[offset : offset + limit]
        prefetch_lookups = list(getattr(sliced, "_prefetch_related_lookups", ()) or ())
        page = list(sliced.prefetch_related(None))
        _t2a = time.perf_counter()
        if prefetch_lookups and page:
            prefetch_related_objects(page, *prefetch_lookups)
        _t2 = time.perf_counter()
        context = _build_order_list_context(request.user, page)
        _t3 = time.perf_counter()
        serializer = OrderListSerializer(page, many=True, context=context)
        results_data = serializer.data
        _t4 = time.perf_counter()
        data = {
            "results": results_data,
            "count": total_count,
            "sales_status_counts": sales_status_counts,
        }
        if is_sheet:
            for obj, row in zip(page, results_data):
                row["contact_count"] = getattr(obj, "contact_count", 0) or 0
            data["sheet_stats"] = {
                "total": total_count,
                **_sheet_stats_with_cache(filters),
            }
        logger.info(
            "landing-timing rows=%d repo+counts=%.0fms slice=%.0fms prefetch=%.0fms ctx=%.0fms serialize=%.0fms total=%.0fms",
            len(page),
            (_t1 - _t0) * 1000,
            (_t2a - _t1) * 1000,
            (_t2 - _t2a) * 1000,
            (_t3 - _t2) * 1000,
            (_t4 - _t3) * 1000,
            (_t4 - _t0) * 1000,
        )
    except Exception:
        logger.exception("Failed to query/serialize landing orders, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách đơn landing. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(data)


@extend_schema(
    operation_id="orders_shipment_list",
    request=inline_serializer(
        "ShipmentOrderListRequest",
        fields={
            "limit": drf_serializers.IntegerField(required=False),
            "offset": drf_serializers.IntegerField(required=False),
            "sort": drf_serializers.ChoiceField(
                choices=ORDERS_SORT_VALUES, required=False
            ),
            "q": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "ShipmentOrderListResponse",
            fields={
                "results": OrderListSerializer(many=True),
                "count": drf_serializers.IntegerField(),
            },
        ),
        500: OpenApiResponse(description="Lỗi truy vấn/serialize danh sách vận đơn"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("shipments")])
@never_cache
def list_shipment_orders(request):
    """Trang Vận đơn (Kho) — khóa cứng đơn có mã vận đơn + sale đã lên đơn."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    source = request.data
    sort_value = _parse_sort(request, default="newest")
    filters = {
        "q": source.get("q", ""),
        "status": _parse_multi_query_values(request, "status"),
        "invoice_status": _parse_multi_query_values(request, "invoice_status"),
        "delivery_business_status": _parse_multi_query_values(request, "delivery_business_status"),
        "order_type": _parse_multi_query_values(request, "order_type"),
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        # "Ngày tạo trên KiotViet" — filter parity với /api/orders.
        "kiotviet_purchase_date_from": source.get("kiotviet_purchase_date_from", ""),
        "kiotviet_purchase_date_to": source.get("kiotviet_purchase_date_to", ""),
        # Khóa cứng — không phụ thuộc client gửi gì.
        "has_shipping_code": "yes",
        "sales_status": ["created_order"],
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }
    filters, scope_empty = _apply_user_channel_scope(filters, request.user, "shipments")
    if scope_empty:
        return _empty_orders_response()

    try:
        queryset, total_count, offset, limit = OrderRepository.list_orders(filters)
        queryset = apply_orders_sort(queryset, sort_value)
        page = list(queryset[offset : offset + limit])
        context = _build_order_list_context(request.user, page)
        serializer = OrderListSerializer(page, many=True, context=context)
        data = {"results": serializer.data, "count": total_count}
    except Exception:
        logger.exception("Failed to query/serialize shipment orders, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách vận đơn. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(data)
