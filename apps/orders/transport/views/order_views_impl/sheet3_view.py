"""Sheet 3 follow-up list — đơn thiếu thông tin, không phải KOC, chưa archived.

Board Tư vấn CỐ Ý dính: đơn đã chốt (closed/closed_express/...) vẫn ở lại board
cho sale nhìn thấy (yêu cầu 2026-08-11) — chỉ "archived" (kết quả cron
autoclose_followups) mới đẩy đơn ra, xem ``BOARD_EXIT_SALES_STATUSES``.
"""
import logging
import time

from django.db.models import Count
from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.db_guards import single_flight
from apps.users.permissions import ORDER_SECTIONS, require_section

from apps.orders.followup import annotate_contact_aggregates, compute_temperature
from apps.orders.models import MARKETPLACE_ORDER_SOURCES
from apps.orders.repositories import OrderRepository
from apps.orders.serializers import OrderListSerializer
from apps.orders.sheet_rules import BOARD_EXIT_SALES_STATUSES

from ._view_helpers import (
    _apply_user_channel_scope,
    _build_order_list_context,
    _empty_orders_response,
    _parse_multi_query_values,
    _parse_required_pagination,
)

logger = logging.getLogger(__name__)

_EMPTY_TEMP_COUNTS = {"hot": 0, "warm": 0, "cold": 0}


# Body filter giống các list view khác; response wrapper phân trang kèm
# temperature_counts (hot/warm/cold) + sales_status_counts (đếm theo mã).
@extend_schema(
    request=inline_serializer(
        "Sheet3OrdersRequest",
        fields={
            "limit": drf_serializers.IntegerField(required=False),
            "offset": drf_serializers.IntegerField(required=False),
            "q": drf_serializers.CharField(required=False),
            "sort": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "Sheet3OrdersResponse",
            fields={
                "results": OrderListSerializer(many=True),
                "count": drf_serializers.IntegerField(),
                "temperature_counts": drf_serializers.DictField(
                    child=drf_serializers.IntegerField()
                ),
                "sales_status_counts": drf_serializers.DictField(
                    child=drf_serializers.IntegerField()
                ),
            },
        ),
        500: OpenApiResponse(description="Lỗi truy vấn/serialize danh sách Sheet 3"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
@never_cache
def list_sheet3_orders(request):
    """Danh sách Sheet 3 — đơn thiếu thông tin (không có địa chỉ/tên/SĐT),
    nguồn bất kỳ trừ KOC, chưa "archived". Đơn đã chốt (closed/closed_express/...)
    vẫn nằm trong board — chỉ "archived" (cron autoclose_followups) mới loại đơn
    ra khỏi board (yêu cầu 2026-08-11).
    Trả thêm temperature (hot/warm/cold), contact_count, last_contact_at cho mỗi dòng
    và temperature_counts tổng toàn bộ tập (không chỉ trang hiện tại).
    """
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    source = request.data
    # Bộ lọc trạng thái sale (chip) — lọc trong phạm vi board, không bỏ ràng buộc cố định.
    sales_status = _parse_multi_query_values(request, "sales_status")
    filters = {
        "q": source.get("q", ""),
        "staff": _parse_multi_query_values(request, "staff"),
        "channel": _parse_multi_query_values(request, "channel"),
        "channel_type": _parse_multi_query_values(request, "channel_type"),
        "product": _parse_multi_query_values(request, "product"),
        "fulfillment_warehouse": _parse_multi_query_values(request, "fulfillment_warehouse"),
        # Khóa cứng: chỉ đơn có nguồn, loại trừ KOC + đơn sàn TMĐT, thiếu thông tin.
        "order_source": "",
        "order_source_present": "true",
        "order_source_exclude": ["koc", *MARKETPLACE_ORDER_SOURCES],
        # Board Tư vấn: thiếu thông tin HOẶC đổ về không có địa chỉ (cờ bất biến).
        "consulting": "only",
        # Thanh chip lọc dùng chung (orders-filter-registry) gửi has_address ở mọi tab
        # — parse để không rơi vào lỗi "UI có mà không lọc" (filter parity giữa các
        # list endpoint).
        "has_address": source.get("has_address", ""),
        # "Phiếu trả hàng" + "Mã đơn thu hồi" cùng nằm trong registry chip dùng chung
        # — parse CẢ HAI vì filter parity (has_recall_code từng bị bỏ sót ở đây,
        # audit 2026-07-15b).
        "has_return_slip": source.get("has_return_slip", ""),
        "has_recall_code": source.get("has_recall_code", ""),
        # Cùng registry chip trên, filter parity với /api/orders (Task 4) — bỏ sót ở
        # đây thì chip lọc nhãn KOC trên UI im lặng không tác dụng ở board Sheet 3.
        "koc_order_type": _parse_multi_query_values(request, "koc_order_type"),
        "has_call_recording": source.get("has_call_recording", ""),
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        # Bộ lọc "Ngày hoàn hàng" (OrdersFilterPanel hiển thị ở mọi tab Landing kể cả
        # Sheet 3) — đọc & áp như list_orders, nếu thiếu thì FE gửi mà list không lọc.
        "return_date_from": source.get("return_date_from", ""),
        "return_date_to": source.get("return_date_to", ""),
        # "Ngày tạo trên KiotViet" — filter parity với /api/orders.
        "kiotviet_purchase_date_from": source.get("kiotviet_purchase_date_from", ""),
        "kiotviet_purchase_date_to": source.get("kiotviet_purchase_date_to", ""),
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }

    filters, scope_empty = _apply_user_channel_scope(filters, request.user)
    if scope_empty:
        return _empty_orders_response(
            {"temperature_counts": dict(_EMPTY_TEMP_COUNTS), "sales_status_counts": {}}
        )

    offset = pagination["offset"]
    limit = pagination["limit"]

    try:
        # Cache các aggregate toàn tập cùng khuôn + cùng knob với _count_with_cache
        # (ORDERS_LIST_COUNT_CACHE_TTL): sheet 3 duyệt CẢ tập bằng Python cho
        # temperature_counts mỗi lần load — nặng nhất trong các list view, và N tab
        # reload đồng nhịp theo `order.created` (bão gate 2026-08-04 09:13).
        from django.conf import settings
        from django.core.cache import cache

        ttl = int(getattr(settings, "ORDERS_LIST_COUNT_CACHE_TTL", 0) or 0)
        # `filters` không chứa trục sales_status (áp riêng bên dưới) nên nó chính là
        # key cho các aggregate "trước chip"; key "sau chip" phải cộng thêm trục đó.
        filters_with_ss = {**filters, "sales_status": sales_status}

        # Tập board: lọc theo ràng buộc cố định + loại trừ trạng thái đã đóng,
        # CHƯA áp bộ lọc trạng thái sale (để đếm chip ổn định khi bật/tắt).
        base_all = OrderRepository._apply_list_filters(
            OrderRepository._base_orders_queryset(include_cancelled=False),
            filters,
        )
        base_all = base_all.exclude(assessment__sales_status_code__in=BOARD_EXIT_SALES_STATUSES)

        # --- sales_status_counts trên tập board (chưa lọc theo trạng thái sale) ---
        ss_cache_key = None
        sales_status_counts: dict[str, int] | None = None
        if ttl > 0:
            ss_cache_key = OrderRepository._filters_cache_key("orders:sheet3_ss_counts:", filters)
            if ss_cache_key is not None:
                sales_status_counts = cache.get(ss_cache_key)
        if sales_status_counts is None:
            sales_status_counts = {}
            for row in base_all.values("assessment__sales_status_code").annotate(c=Count("id")):
                code = row["assessment__sales_status_code"] or "__empty__"
                sales_status_counts[code] = sales_status_counts.get(code, 0) + row["c"]
            if ss_cache_key is not None:
                cache.set(ss_cache_key, sales_status_counts, ttl)

        # Áp bộ lọc chip trạng thái sale cho danh sách thực tế hiển thị.
        base = OrderRepository._apply_sales_status_filter(base_all, sales_status)

        # --- temperature_counts trên TOÀN BỘ tập đã lọc (không phân trang) ---
        # `now` trôi tối đa TTL giây trong cache — days_since tính theo NGÀY nên
        # sai số 30s không đổi kết quả (trừ đúng khoảnh khắc qua ngày, chấp nhận).
        now = timezone.now()
        temp_cache_key = None
        temperature_counts: dict[str, int] | None = None
        if ttl > 0:
            temp_cache_key = OrderRepository._filters_cache_key(
                "orders:sheet3_temp_counts:", filters_with_ss
            )
            if temp_cache_key is not None:
                temperature_counts = cache.get(temp_cache_key)

        def _compute_temperature_counts() -> dict[str, int]:
            counts = {"hot": 0, "warm": 0, "cold": 0}
            agg_qs = annotate_contact_aggregates(base)
            for row in agg_qs.values(
                "id",
                "created_at",
                "contact_count",
                "last_contact_at",
                "assessment__sales_status_code",
            ):
                cnt = row["contact_count"] or 0
                last_dt = row["last_contact_at"] or row["created_at"]
                days = max(0, (now - last_dt).days)
                temp = compute_temperature(
                    contact_count=cnt,
                    days_since=days,
                    sales_status=row["assessment__sales_status_code"],
                )
                counts[temp] += 1
            return counts

        if temperature_counts is None:
            if temp_cache_key is None:
                temperature_counts = _compute_temperature_counts()
            else:
                # Chống stampede (audit 2026-08-19 P2): N tab reload đồng nhịp sau
                # `order.created` cùng miss → N lần duyệt toàn tập bằng Python.
                # Request giành được lock tính + ghi cache TRONG lock; request khác
                # chờ ngắn rồi đọc lại. Vẫn miss sau khi chờ (compute chậm/Redis
                # chết) thì tự tính — fail-open, không bao giờ trả thiếu dữ liệu.
                with single_flight(f"{temp_cache_key}:lock", 30) as acquired:
                    if acquired:
                        temperature_counts = _compute_temperature_counts()
                        cache.set(temp_cache_key, temperature_counts, ttl)
                    else:
                        for _ in range(6):
                            time.sleep(0.25)
                            temperature_counts = cache.get(temp_cache_key)
                            if temperature_counts is not None:
                                break
                if temperature_counts is None:
                    temperature_counts = _compute_temperature_counts()
                    cache.set(temp_cache_key, temperature_counts, ttl)

        # --- Trang kết quả ---
        # Key count phải gồm trục sales_status (base đã áp chip) — filters trần
        # thiếu trục đó sẽ đâm chung key giữa các lựa chọn chip khác nhau.
        total_count = OrderRepository._count_with_cache(base, filters_with_ss)

        # Wrap với related + kiotviet annotations, SAU ĐÓ annotate contact aggregates
        # để không bị ghi đè bởi _wrap_with_related_and_annotations.
        queryset = annotate_contact_aggregates(
            OrderRepository._wrap_with_related_and_annotations(
                base, with_journey_events=False
            )
        )
        queryset = queryset.order_by("last_contact_at", "created_at", "id")

        page = list(queryset[offset : offset + limit])
        # full_crm: panel đánh giá sheet3 (BlockCrmRating) dùng chung với trang chi
        # tiết — cần cả ai_filled_fields/extra_fields/ai_open_insights/labels.
        context = _build_order_list_context(request.user, page, full_crm=True)
        serializer = OrderListSerializer(page, many=True, context=context)
        results_data = list(serializer.data)

        # Gắn thêm temperature fields vào từng dòng.
        for obj, row in zip(page, results_data):
            cnt = getattr(obj, "contact_count", None) or 0
            last_dt = getattr(obj, "last_contact_at", None) or obj.created_at
            days = max(0, (now - last_dt).days)
            assessment = getattr(obj, "assessment", None)
            ss_code = getattr(assessment, "sales_status_code", None) if assessment else None
            temp = compute_temperature(
                contact_count=cnt,
                days_since=days,
                sales_status=ss_code,
            )
            row["temperature"] = temp
            row["contact_count"] = cnt
            row["last_contact_at"] = last_dt.isoformat() if getattr(obj, "last_contact_at", None) else None

        data = {
            "results": results_data,
            "count": total_count,
            "temperature_counts": temperature_counts,
            "sales_status_counts": sales_status_counts,
        }
    except Exception:
        logger.exception("Failed to query/serialize sheet3 orders, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách đơn Sheet 3. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(data)
