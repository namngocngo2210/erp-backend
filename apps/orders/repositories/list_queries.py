"""Truy vấn danh sách đơn: ghép bộ lọc (``apply_list_filters``), list có phân
trang, đếm theo trạng thái, lịch sử theo SĐT/khách.

Tách nguyên trạng từ ``apps/orders/repositories.py`` (audit T3/P2-3) — KHÔNG đổi
hành vi, chỉ dời chỗ. ``_apply_list_filters`` gốc (541 dòng) được chia thành các
hàm theo nhóm ở list_search.py / list_filter_attrs.py / list_filter_flags.py;
``apply_list_filters`` dưới đây ghép lại ĐÚNG THỨ TỰ áp filter cũ.
"""
from django.db.models import Count, Exists, OuterRef, Q, Subquery

from apps.orders.models import Order, OrderKiotVietJob, OrderStatus
from apps.orders.reporting import CANCELLED_ORDER_Q

from .list_filter_attrs import (
    apply_location_filters,
    apply_product_filter,
    apply_sheet_filters,
    apply_staff_and_source_filters,
    apply_status_and_channel_filters,
    apply_type_filters,
)
from .list_filter_flags import (
    apply_assessment_status_filter,
    apply_assessment_status_filters,
    apply_date_range_filters,
    apply_flag_filters,
    apply_relation_filters,
    apply_sales_status_filter,
)
from .list_search import apply_keyword_search


def apply_list_filters(queryset, filters: dict):
    """Áp toàn bộ bộ lọc danh sách đơn lên `queryset` — thứ tự cố định:
    q → status/channel → product → staff/source → sheet → kho → loại đơn → cờ →
    quan hệ → assessment status → khoảng ngày."""
    raw_q = str(filters.get("q") or "").strip()
    queryset = apply_keyword_search(queryset, raw_q)
    queryset = apply_status_and_channel_filters(queryset, filters)
    queryset = apply_product_filter(queryset, filters)
    queryset = apply_staff_and_source_filters(queryset, filters)
    queryset = apply_sheet_filters(queryset, filters, raw_q)
    queryset = apply_location_filters(queryset, filters)
    queryset = apply_type_filters(queryset, filters)
    queryset = apply_flag_filters(queryset, filters)
    queryset = apply_relation_filters(queryset, filters)
    queryset = apply_assessment_status_filters(queryset, filters)
    return apply_date_range_filters(queryset, filters, raw_q)


class OrderListQueriesMixin:
    """List/filter/search + đếm trạng thái + lịch sử đơn của khách."""

    _apply_list_filters = staticmethod(apply_list_filters)
    _apply_assessment_status_filter = staticmethod(apply_assessment_status_filter)
    _apply_sales_status_filter = staticmethod(apply_sales_status_filter)

    @staticmethod
    def _filters_request_cancelled(filters: dict) -> bool:
        raw = filters.get("invoice_status") or []
        if not isinstance(raw, list):
            raw = [raw]
        for value in raw:
            try:
                if int(value) == 2:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    @classmethod
    def _should_include_cancelled(cls, filters: dict) -> bool:
        """Đơn huỷ (status=6 / invoice_status=2) được đưa vào base khi: đang xem tab
        "Đã huỷ" (invoice_status=2), HOẶC đang search (q). Search bỏ qua mọi bộ lọc
        khung (ngày, show_orders) để "quét toàn bộ lịch sử" — nếu vẫn loại đơn huỷ thì
        tìm SĐT/mã của một đơn đã huỷ sẽ trả rỗng ("có trong DB mà search không ra").
        """
        if cls._filters_request_cancelled(filters):
            return True
        return bool(str(filters.get("q") or "").strip())

    @classmethod
    def search_for_lookup(cls, q: str, *, limit: int = 20):
        """Tìm đơn theo mã hóa đơn/mã đơn/mã vận đơn/SĐT (kể cả đơn đã hủy).

        Public wrapper cho các nơi ngoài repository cần "khớp thủ công theo đơn"
        (vd flow_history_views.flow_order_lookup) — tránh gọi thẳng
        _base_orders_queryset/_apply_list_filters (private, chỉ dùng nội bộ module).
        """
        base = cls._base_orders_queryset(include_cancelled=True)
        rows = (
            cls._apply_list_filters(base, {"q": q})
            .select_related("kiotviet_channel")
            .order_by("-created_at")
        )
        return rows[:limit]

    @classmethod
    def list_orders(cls, filters: dict):
        include_cancelled = cls._should_include_cancelled(filters)
        base = cls._apply_list_filters(
            cls._base_orders_queryset(include_cancelled=include_cancelled), filters
        )
        total_count = cls._count_with_cache(base, filters)
        # KiotViet job state is attached per-page in 2 batched queries (see
        # kiotviet_job_maps / _build_order_list_context) instead of 4 correlated
        # Subquery/Exists annotations evaluated per row in this list query.
        queryset = cls._with_related_crm(
            base, with_journey_events=False, lean_list_columns=True
        ).order_by("-created_at")
        limit = int(filters.get("limit", 50))
        offset = int(filters.get("offset", 0))
        return queryset, total_count, offset, limit

    @classmethod
    def list_orders_with_status_counts(cls, filters: dict):
        """Build the base queryset once, derive sales_status_counts from it, then apply
        the sales_status filter for the actual page slice. Used by the landing list view
        which needs both badge counts and a paged result in a single request.
        """
        filters_no_ss = {**filters, "sales_status": []}
        include_cancelled = cls._should_include_cancelled(filters)
        base_no_ss = cls._apply_list_filters(
            cls._base_orders_queryset(include_cancelled=include_cancelled), filters_no_ss
        )
        # Badge counts cache cùng khuôn + cùng TTL với _count_with_cache: GROUP BY
        # toàn tập chạy lại mỗi lần load landing/sheet — landing là list ĐƯỢC GỌI
        # DÀY NHẤT giờ thường (CSKH), cùng bệnh với status_counts_for bên trang
        # Đơn hàng. Key theo filters_no_ss vì counts tính trên tập bỏ sales_status.
        from django.conf import settings

        ttl = int(getattr(settings, "ORDERS_LIST_COUNT_CACHE_TTL", 0) or 0)
        ss_cache_key = None
        sales_status_counts: dict[str, int] | None = None
        if ttl > 0:
            from django.core.cache import cache

            ss_cache_key = cls._filters_cache_key(
                "orders:sales_status_counts:", filters_no_ss
            )
            if ss_cache_key is not None:
                sales_status_counts = cache.get(ss_cache_key)
        if sales_status_counts is None:
            counts_rows = list(
                base_no_ss.values("assessment__sales_status_code").annotate(c=Count("id"))
            )
            sales_status_counts = {}
            for row in counts_rows:
                code = row["assessment__sales_status_code"] or "__empty__"
                sales_status_counts[code] = sales_status_counts.get(code, 0) + row["c"]
            if ss_cache_key is not None:
                from django.core.cache import cache

                cache.set(ss_cache_key, sales_status_counts, ttl)

        data_base = cls._apply_sales_status_filter(
            base_no_ss, filters.get("sales_status")
        )
        total_count = cls._count_with_cache(data_base, filters)
        # See list_orders: per-page batched KiotViet job state, not per-row annotations.
        queryset = cls._with_related_crm(
            data_base, with_journey_events=False, lean_list_columns=True
        ).order_by("-created_at")
        limit = int(filters.get("limit", 50))
        offset = int(filters.get("offset", 0))
        return queryset, total_count, offset, limit, sales_status_counts

    @classmethod
    def status_counts_for(cls, filters: dict) -> dict[str, int]:
        """Đếm số đơn theo Order.status cho thanh chip lọc trạng thái. Mỗi chip phản
        ánh "chọn chip này thì ra bao nhiêu đơn" nên count tính trên tập khớp MỌI bộ
        lọc hiện tại TRỪ chính status/invoice_status (giống cách sales_status_counts bỏ
        sales_status). Các key lifecycle (0..5, 7..13) đếm trên tập non-cancelled như
        danh sách thường; key "6" là số đơn đã hủy (invoice_status=2 OR status=6), khớp
        tab "Đã hủy" — vì trường status không bao giờ lọc ra đơn hủy (_apply_list_filters
        luôn strip status=6 và base loại status=6/invoice_status=2).

        CONTRACT (Task 7 FE chips phụ thuộc vào đây): hàm này luôn strip ĐÚNG 2 trục
        filter — "status" và "invoice_status" — trước khi đếm, không strip thêm trục
        nào khác. Đổi tập trục strip ở đây sẽ đổi ý nghĩa mọi chip, phải đồng bộ với FE.

        Trước đây cài bằng 2 query riêng (1 GROUP BY non-cancelled + 1 COUNT
        cancelled) → filter phức tạp (product/assessment Exists, q search) bị áp lại
        2 lần, vật hoá base queryset 2 lần. Giờ gộp về 1 base queryset + 1 query
        `aggregate(Count(filter=...))` (1 round-trip, mỗi Count là 1 mệnh đề FILTER
        trong cùng câu SQL) — số liệu giữ nguyên như bản 2-query cũ (xem
        test_status_counts_for.py).

        Cache ngắn hạn cùng khuôn + cùng TTL với _count_with_cache
        (ORDERS_LIST_COUNT_CACHE_TTL): aggregate này quét toàn tập khớp bộ lọc và
        chạy lại MỖI lần load list — trong burst `order.created` (bão gate
        2026-08-04 09:13) N tab cùng bộ lọc là N lần quét y hệt nhau. Chip có thể
        trễ tối đa TTL giây, cùng độ trễ đã chấp nhận cho nhãn "tổng N đơn".
        """
        from django.conf import settings

        ttl = int(getattr(settings, "ORDERS_LIST_COUNT_CACHE_TTL", 0) or 0)
        cache_key = None
        if ttl > 0:
            from django.core.cache import cache

            cache_key = cls._filters_cache_key("orders:status_counts:", filters)
            if cache_key is not None:
                cached = cache.get(cache_key)
                if cached is not None:
                    return cached

        base_all = cls._apply_list_filters(
            cls._base_orders_queryset(include_cancelled=True),
            {**filters, "status": [], "invoice_status": []},
        )
        cancelled_q = CANCELLED_ORDER_Q
        lifecycle_statuses = [s for s in OrderStatus.values if s != 6]
        aggregates = {
            f"status_{status}": Count("id", filter=Q(status=status) & ~cancelled_q)
            for status in lifecycle_statuses
        }
        aggregates["cancelled"] = Count("id", filter=cancelled_q)
        row = base_all.aggregate(**aggregates)

        counts: dict[str, int] = {}
        for status in lifecycle_statuses:
            c = row[f"status_{status}"]
            if c:
                counts[str(status)] = c
        counts["6"] = row["cancelled"]
        if cache_key is not None:
            from django.core.cache import cache

            cache.set(cache_key, counts, ttl)
        return counts

    @classmethod
    def list_history_by_phone(cls, phone_normalized: str):
        if not phone_normalized:
            return Order.objects.none()
        latest_job_subquery = OrderKiotVietJob.objects.filter(order_id=OuterRef("pk")).order_by("-created_at", "-id")
        return cls._with_related_crm(
            cls._exclude_hidden_branches(
                Order.objects.filter(deleted_at__isnull=True).exclude(status=6).exclude(invoice_status=2)
            )
        ).annotate(
            is_kiotviet_processing=Exists(cls._active_kiotviet_job_subquery()),
            latest_kiotviet_job_status=Subquery(latest_job_subquery.values("status")[:1]),
            latest_kiotviet_job_action=Subquery(latest_job_subquery.values("action")[:1]),
            latest_kiotviet_job_error=Subquery(latest_job_subquery.values("error_message")[:1]),
            latest_kiotviet_job_requested_by=Subquery(latest_job_subquery.values("requested_by_id")[:1]),
        ).filter(
            # Tham số vào ĐÃ chuẩn hóa (view gọi `_normalize_phone`), nên phải so với
            # cột ĐÃ chuẩn hóa. Cách cũ `phone__icontains=<số đã chuẩn hóa>` sai cả 2 mặt:
            # (1) SAI KẾT QUẢ — đơn lưu SĐT thô dạng `+84901234567` không chứa chuỗi
            #     `0901234567` nên bị BỎ SÓT khỏi lịch sử mua của khách;
            # (2) CHẬM — UPPER(phone) LIKE bỏ qua trigram GIN `order_phone_trgm_idx`,
            #     seq-scan 500k dòng.
            # `phone_normalized` do `Order.save()` derive nên luôn có, và so BẰNG thì
            # dùng thẳng B-tree `order_phone_norm_idx` (audit 2026-07-26 C2).
            Q(customer__phone_normalized=phone_normalized)
            | Q(phone_normalized=phone_normalized)
        ).order_by("-created_at")

    @classmethod
    def list_customer_orders(cls, customer_id: int):
        latest_job_subquery = OrderKiotVietJob.objects.filter(order_id=OuterRef("pk")).order_by("-created_at", "-id")
        return cls._with_related_crm(
            cls._exclude_hidden_branches(
                Order.objects.filter(customer_id=customer_id, deleted_at__isnull=True).exclude(status=6).exclude(invoice_status=2)
            ),
            with_journey_events=False,
        ).annotate(
            is_kiotviet_processing=Exists(cls._active_kiotviet_job_subquery()),
            latest_kiotviet_job_status=Subquery(latest_job_subquery.values("status")[:1]),
            latest_kiotviet_job_action=Subquery(latest_job_subquery.values("action")[:1]),
            latest_kiotviet_job_error=Subquery(latest_job_subquery.values("error_message")[:1]),
            latest_kiotviet_job_requested_by=Subquery(latest_job_subquery.values("requested_by_id")[:1]),
        ).order_by("-created_at")

    @staticmethod
    def customer_ids_assigned_to(staff_id: int | None, customer_ids) -> set[int]:
        """Trong `customer_ids`, khách nào có ÍT NHẤT một đơn do `staff_id` phụ trách.

        Dùng để quyết định che SĐT trên các màn hình cấp KHÁCH HÀNG (danh sách khách,
        KH thân thiết, theo dõi resale, hồ sơ 360) — nơi không có sẵn `Order.staff_id`
        của một dòng như trang đơn. MỘT query cho cả trang, không phải mỗi dòng một
        lần (xem apps/users/phone_visibility.py).
        """
        ids = {int(cid) for cid in customer_ids if cid is not None}
        if not staff_id or not ids:
            return set()
        return set(
            Order.objects.filter(
                deleted_at__isnull=True, staff_id=staff_id, customer_id__in=ids
            )
            .values_list("customer_id", flat=True)
            .distinct()
        )
