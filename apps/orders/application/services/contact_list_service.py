"""ContactListService — 1 dòng/khách cho trang Danh sách contact (Sale).

Query gộp trên `orders` (đơn mới nhất mỗi khách qua DISTINCT ON), KHÔNG có bảng
state materialize riêng — tránh bài toán drift đã gặp với các trường denormalize.
Xem docs/sale-contacts-page-design.md §3.

Layering: application service thuần — không có mối quan tâm HTTP ở đây.
"""
from __future__ import annotations

import time as _time_mod
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Exists, Max, OuterRef, Q
from django.utils import timezone

from apps.common import db_lookups  # noqa: F401  (đăng ký lookup `__ilike_contains`)
from apps.customers.models import ContactActivity
from apps.orders.domain.contact_rules import derive_action_needed
from apps.orders.models import (
    MARKETPLACE_ORDER_SOURCES,
    Order,
    OrderItem,
    OrderJourneyEvent,
    OrderStatus,
)
from apps.orders.subscribers import STATUS_CHANGED_EVENT
from apps.users.phone_visibility import is_public_phone_source, phone_visibility_for
from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

from .contact_list_operation import (
    apply_operation_bucket_filter,
    compute_operation_counts,
    operation_payload,
    operation_state_map,
    refresh_operation_buckets,
)

# Nguồn loại khỏi luồng sale landing: đơn sàn TMĐT + koc (cùng cách các trang
# Landing/Sheet loại nguồn, xem apps/orders/models.py dòng ~97).
_EXCLUDED_ORDER_SOURCES = [*MARKETPLACE_ORDER_SOURCES, "koc"]

# event_type ghi vào OrderJourneyEvent được tính là "đã tương tác" với khách
# (thay đổi trạng thái sale / tạo assessment lần đầu).
_JOURNEY_ACTIVITY_EVENT_TYPES = ["sales_status_changed", "assessment_created"]

# Cửa sổ mặc định khi caller không truyền ngày: không có chốt này, gọi thẳng API bỏ
# param là DISTINCT ON toàn bảng orders rồi materialize hết ra Python (~7s prod) —
# chốt chặn ở FE (mặc định this_month) không bảo vệ được endpoint (audit 2026-07-15b).
_DEFAULT_WINDOW_DAYS = 92

# to_code của journey event khi đơn chuyển sang Giao thành công. Subscriber ghi
# str(int) nên phải so sánh bằng chuỗi, không phải bằng OrderStatus.
_GTC_TO_CODE = str(OrderStatus.DELIVERED_SUCCESS.value)

# Số dòng mỗi lô khi job export dựng item. Chọn 500 — cùng bậc với trang lớn nhất
# của màn hình (100) nhân lên, đủ để cắt số round-trip mà không giữ quá nhiều Order
# object cùng lúc trong RAM worker.
_EXPORT_CHUNK = 500


def _apply_contacts_scope(queryset, *, user):
    """Áp record-level scope của section `contacts` — ĐỦ 4 mức DataScope.

    Trước đây chỗ này chỉ phân biệt nhị phân OWN vs không-OWN: user được cấp
    contacts:view scope CHANNEL/DEPARTMENT (cấu hình tự nhiên nhất cho sale) không bị
    filter gì cả → thấy toàn bộ danh bạ + lịch sử liên hệ của mọi khách, và user
    CHANNEL chưa gán kênh nào (đáng lẽ 0 khách) lại thấy tất cả (audit 2026-07-15b).
    Cùng quy ước với `_apply_user_channel_scope` của trang Đơn hàng.
    """
    codes = resolved_channel_codes(user, "contacts")
    if codes is None:
        return queryset
    if codes is OWN_SCOPE_SENTINEL:
        return queryset.filter(staff_id=user.id)
    if not codes:
        return queryset.none()
    return queryset.filter(channel__code__in=codes)


def _waiting_sort_key(row: dict) -> tuple:
    """Khóa sắp xếp danh sách contact = cột "Thời gian chờ" trên UI.

    (không_có_tác_nghiệp, mốc_chờ, ngày_data_về): dòng có tác nghiệp xếp trước theo
    mốc chờ sớm nhất; dòng `none` (ô chờ trống) dồn cuối, trong nhóm đó theo ngày
    data về. Tie-break bằng created_at để thứ tự ổn định giữa các trang."""
    no_action = row["action_needed"] == "none"
    return (no_action, row["waiting_since"], row["created_at"])


def _items_from_sku_string(raw: str) -> list[dict]:
    """Tách chuỗi SKU gộp của Order thành dòng hàng tối thiểu.

    Đơn sàn TMĐT bị loại khỏi danh sách contact từ `_EXCLUDED_ORDER_SOURCES` nên
    không bao giờ rơi vào nhánh này. Dân số thật là đơn import cũ / đơn tạo
    trước khi có OrderItem, chỉ có Order.sku là chuỗi join. Hai điểm ghi dùng
    dấu phân cách khác nhau (", " ở order_service_helpers, "," ở import_excel)
    nên tách theo dấu phẩy rồi strip.
    """
    return [
        {"sku": piece, "product_name": "", "quantity": 0}
        for piece in (part.strip() for part in (raw or "").split(","))
        if piece
    ]


class ContactListService:
    @staticmethod
    def _resolve_rows(
        *,
        user,
        date_from: date | None = None,
        date_to: date | None = None,
        q: str = "",
        channel_ids: list[int] | None = None,
        order_sources: list[str] | None = None,
        staff_ids: list[int] | None = None,
        product_ids: list[int] | None = None,
        action_needed: list[str] | None = None,
        interaction: str = "",
        customer_type: str = "",
        operation_bucket: str | None = None,
        customer_ids: list[int] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """→ (scoped_rows, filtered_rows) — ĐIỂM CHÂN LÝ DUY NHẤT của "tập contact
        khớp bộ lọc".

        `scoped_rows` đã áp interaction + customer_type (nền để đếm chip);
        `filtered_rows` áp thêm action_needed + operation_bucket và đã sắp xếp theo
        mốc chờ (`waiting_since`) tăng dần — chờ lâu nhất đứng đầu.

        Cả list endpoint lẫn export Excel đều đi qua đây: tách thành hai đường lọc rời
        là cách chắc chắn nhất để file xuất lệch màn hình khi thêm một trục lọc mới.
        """
        # Ép cửa sổ ngày server-side: cả hai đầu trống → mặc định N ngày gần nhất;
        # thiếu đầu from → suy từ đầu to. from tường minh là lựa chọn của user, giữ nguyên.
        # Resolve default TRƯỚC khi vào tầng cache để "hôm nay, không truyền ngày" và
        # "hôm nay, truyền đúng cửa sổ mặc định" ra CÙNG một cache key.
        if date_from is None:
            anchor = date_to if date_to is not None else timezone.localdate()
            date_from = anchor - timedelta(days=_DEFAULT_WINDOW_DAYS)

        enriched_rows = ContactListService._enriched_rows_cached(
            user=user,
            date_from=date_from,
            date_to=date_to,
            q=q,
            channel_ids=channel_ids,
            order_sources=order_sources,
            staff_ids=staff_ids,
            product_ids=product_ids,
            customer_ids=customer_ids,
        )
        if not enriched_rows:
            return [], []

        # scoped_rows là nền để đếm chip: đã áp interaction+customer_type (design §5:
        # y = "tổng contact thuộc nhóm TRONG BỘ LỌC") nhưng KHÔNG áp action_needed/
        # operation_bucket — bấm 1 chip không được làm co số các chip còn lại.
        scoped_rows = ContactListService._apply_scope_filters(
            enriched_rows, interaction=interaction, customer_type=customer_type
        )
        filtered_rows = ContactListService._apply_action_needed_filter(
            scoped_rows, action_needed=action_needed
        )
        filtered_rows = apply_operation_bucket_filter(
            filtered_rows, operation_bucket=operation_bucket
        )
        # Sắp theo cột "Thời gian chờ": mốc chờ sớm nhất (chờ lâu nhất) đứng đầu.
        # Dòng không có tác nghiệp (action_needed = none, ô chờ hiện "—") xếp cuối
        # theo ngày data về. Cả list lẫn export dùng chung (hotfix 2026-09-15).
        filtered_rows.sort(key=_waiting_sort_key)
        return scoped_rows, filtered_rows

    @staticmethod
    def list_contacts(
        *,
        user,
        limit: int = 20,
        offset: int = 0,
        **filters,
    ) -> dict:
        """→ {"items": [dict], "total": int, "action_counts": {...}, "operation_counts": {...}}

        `filters` đúng bộ tham số của `_resolve_rows` (date_from/date_to/q/channel_ids/
        order_sources/staff_ids/product_ids/action_needed/interaction/customer_type/
        operation_bucket) — nhận qua kwargs để không phải chép lại danh sách 11 tham số
        ở hai chỗ (chép lệch là đúng kiểu lỗi "lọc im lặng không chạy" đã cắn ở trang Đơn).
        """
        scoped_rows, filtered_rows = ContactListService._resolve_rows(user=user, **filters)

        # Hợp đồng: action_counts/operation_counts LUÔN có key "all" kể cả khi rỗng —
        # cả hai hàm đếm tự set key "all" nên list rỗng vẫn đúng hợp đồng.
        action_counts = ContactListService._compute_action_counts(scoped_rows)
        operation_counts = compute_operation_counts(scoped_rows)

        total = len(filtered_rows)
        page_rows = filtered_rows[offset : offset + limit]
        items = ContactListService._build_items(page_rows, user=user)

        return {
            "items": items,
            "total": total,
            "action_counts": action_counts,
            "operation_counts": operation_counts,
        }

    @staticmethod
    def count_contacts(*, user, filters: dict) -> int:
        """Số dòng contact khớp bộ lọc — dùng cho `estimate()` của job export.

        Khi cache bật (ORDERS_LIST_COUNT_CACHE_TTL > 0), estimate + write của cùng
        một job export dùng chung entry `_enriched_rows_cached` (cùng user + cùng
        bộ lọc SQL) — pipeline nặng chỉ chạy MỘT lần thay vì hai."""
        _, filtered_rows = ContactListService._resolve_rows(user=user, **filters)
        return len(filtered_rows)

    @staticmethod
    def iter_contact_batches(*, user, filters: dict, chunk_size: int = _EXPORT_CHUNK):
        """Yield từng lô item (shape y hệt `list_contacts()["items"]`) cho job export.

        Pipeline lọc chạy ĐÚNG MỘT LẦN; chỉ phần `_build_items` (join order/customer/
        assessment + đếm đơn) mới chạy theo lô — giữ RAM worker phẳng thay vì
        materialize cả 20.000 item cùng lúc.
        """
        _, filtered_rows = ContactListService._resolve_rows(user=user, **filters)
        for start in range(0, len(filtered_rows), chunk_size):
            yield ContactListService._build_items(
                filtered_rows[start : start + chunk_size], user=user
            )

    # Khuôn key cùng họ với "orders:list_count:" (OrderRepository._filters_cache_key).
    # v2: dòng cache có thêm waiting_since/waiting_basis — đổi prefix để entry cũ
    # (nếu cache đang bật) không gây KeyError.
    _ROWS_CACHE_PREFIX = "orders:contact_rows:v2:"

    @staticmethod
    def _rows_cache_key(
        user,
        *,
        date_from,
        date_to,
        q,
        channel_ids,
        order_sources,
        staff_ids,
        product_ids,
        customer_ids,
    ) -> str | None:
        """Khoá cache của tập enriched rows — hash các trục lọc SQL + SCOPE của user.

        Scope BẮT BUỘC nằm trong khoá (hai user khác scope không được dùng chung
        entry — rò dữ liệu giữa sale, xem docs/cache-latency-optimization §rủi ro).
        Biểu diễn scope bằng chính tập channel code hiệu dụng (user_id chỉ khi scope
        OWN) chứ không phải user_id trần: các sale cùng kênh CHIA SẺ một entry thay
        vì mỗi người một bản.

        Các trục lọc PYTHON (interaction/customer_type/action_needed/operation_bucket)
        CỐ Ý không có mặt: chúng áp SAU cache, nên bấm chip/bucket khác nhau vẫn
        trúng chung một entry. Trả None khi không hash được — caller bỏ cache.
        """
        import hashlib
        import json

        codes = resolved_channel_codes(user, "contacts")
        if codes is None:
            scope = "all"
        elif codes is OWN_SCOPE_SENTINEL:
            scope = f"own:{user.id}"
        else:
            scope = "ch:" + ",".join(sorted(codes))
        key_src = {
            "scope": scope,
            "date_from": date_from,
            "date_to": date_to,
            "q": q,
            "channel_ids": sorted(channel_ids) if channel_ids else None,
            "order_sources": sorted(order_sources) if order_sources else None,
            "staff_ids": sorted(staff_ids) if staff_ids else None,
            "product_ids": sorted(product_ids) if product_ids else None,
            "customer_ids": sorted(customer_ids) if customer_ids else None,
        }
        try:
            raw = json.dumps(key_src, sort_keys=True, default=str)
        except TypeError:
            return None
        return ContactListService._ROWS_CACHE_PREFIX + hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _enriched_rows_cached(*, user, **query_kwargs) -> list[dict]:
        """Tập enriched rows, có cache ngắn hạn (TTL dùng chung
        ORDERS_LIST_COUNT_CACHE_TTL — mặc định 0 = tắt, prod 120s qua compose).

        Đây là điểm nghẽn của trang contact: DISTINCT ON toàn cửa sổ ngày rồi
        materialize + enrich bằng Python — mỗi lượt bấm chip/lật trang trước đây chạy
        lại từ đầu. Cache theo TTL, KHÔNG invalidate theo sự kiện (đơn mới/đổi trạng
        thái trễ tối đa TTL giây trên danh sách — cùng mức chấp nhận với
        status_counts của trang Đơn). Redis chết thì InstrumentedRedisCache fail-soft
        thành cache-miss, pipeline chạy thẳng như cũ.

        Riêng `operation.bucket` phụ thuộc thời điểm đọc → tính lại từ due_at trên
        mỗi cache hit, nên hit/miss trả cùng kết quả tại cùng thời điểm.
        """
        from django.conf import settings

        ttl = int(getattr(settings, "ORDERS_LIST_COUNT_CACHE_TTL", 0) or 0)
        if ttl <= 0:
            return ContactListService._compute_enriched_rows(user=user, **query_kwargs)

        from django.core.cache import cache

        cache_key = ContactListService._rows_cache_key(user, **query_kwargs)
        if cache_key is None:
            return ContactListService._compute_enriched_rows(user=user, **query_kwargs)

        cached = cache.get(cache_key)
        if cached is not None:
            refresh_operation_buckets(cached, now=timezone.now())
            return cached

        # Chống stampede (audit 2026-09-23 perf §3.1): 10 tab sale cùng mở đầu ca
        # sau khi cache hết hạn → 10 lượt pipeline DISTINCT ON 92 ngày y hệt nhau.
        # Cùng khuôn sheet3 temperature_counts: request giành được lock tính + ghi
        # cache; request khác chờ ngắn rồi đọc lại; vẫn miss (compute chậm/Redis
        # chết) thì tự tính — fail-open.
        from apps.common.db_guards import single_flight

        rows = None
        with single_flight(f"{cache_key}:lock", 30) as acquired:
            if acquired:
                rows = ContactListService._compute_enriched_rows(user=user, **query_kwargs)
                # Cache cả kết quả rỗng: tập rỗng cũng tốn nguyên một lượt DISTINCT ON.
                cache.set(cache_key, rows, ttl)
            else:
                for _ in range(6):
                    _time_mod.sleep(0.25)
                    rows = cache.get(cache_key)
                    if rows is not None:
                        refresh_operation_buckets(rows, now=timezone.now())
                        break
        if rows is None:
            rows = ContactListService._compute_enriched_rows(user=user, **query_kwargs)
            cache.set(cache_key, rows, ttl)
        return rows

    @staticmethod
    def _compute_enriched_rows(
        *,
        user,
        date_from,
        date_to,
        q,
        channel_ids,
        order_sources,
        staff_ids,
        product_ids,
        customer_ids,
    ) -> list[dict]:
        """Phần NẶNG của pipeline: base queryset + DISTINCT ON + enrich. Tách riêng
        để `_enriched_rows_cached` bọc cache quanh đúng ranh giới này."""
        base = ContactListService._base_queryset(
            user=user, date_from=date_from, date_to=date_to, q=q
        )
        # Kênh + nguồn đơn là MỘT bộ lọc "Nguồn" (multi-select gộp) trên UI → hợp (OR)
        # giữa hai trục, không phải giao: chọn "Kênh X" + "Zalo" = khách kênh X
        # HOẶC đơn từ Zalo. Chỉ truyền một trục thì vẫn thu hẹp như cũ.
        source_q = Q()
        if channel_ids:
            source_q |= Q(channel_id__in=channel_ids)
        if order_sources:
            source_q |= Q(order_source__in=order_sources)
        if source_q:
            base = base.filter(source_q)
        if staff_ids:
            base = base.filter(staff_id__in=staff_ids)
        if customer_ids:
            # Người dùng tích chọn dòng trên bảng rồi xuất Excel. CỐ Ý là một trục lọc
            # NỮA (giao với các trục khác) chứ không phải đường lấy dữ liệu riêng: đi
            # qua đúng pipeline này thì record-scope RBAC + cửa sổ ngày vẫn áp nguyên,
            # nên khách bị tích mà ngoài quyền người xuất sẽ rớt khỏi kết quả thay vì
            # lọt ra file.
            base = base.filter(customer_id__in=customer_ids)
        if product_ids:
            # Match bằng product_id trên index order_item_product_idx. KHÔNG dùng
            # icontains trên OrderItem.sku: đó là seq-scan ~855k dòng mỗi lần lọc
            # (bài học repositories.py:614-641). Lọc đặt TRƯỚC DISTINCT ON nên dòng
            # trả về là đơn mới nhất CÓ CHỨA sản phẩm, không phải đơn mới nhất nói chung.
            base = base.filter(
                Exists(
                    OrderItem.objects.filter(
                        order_id=OuterRef("pk"), product_id__in=product_ids
                    )
                )
            )

        rows = ContactListService._latest_order_rows(base)
        if not rows:
            return []

        result_customer_ids = [row["customer_id"] for row in rows]
        order_ids = [row["id"] for row in rows]
        last_activity_by_customer = ContactListService._last_activity_map(result_customer_ids)
        last_journey_by_order = ContactListService._last_journey_event_map(order_ids)
        operation_state_by_customer = operation_state_map(result_customer_ids)
        gtc_at_by_order = ContactListService._gtc_at_map(
            [row["id"] for row in rows if row["status"] == OrderStatus.DELIVERED_SUCCESS]
        )
        now = timezone.now()

        enriched_rows = []
        for row in rows:
            last_activity_at = last_activity_by_customer.get(row["customer_id"])
            last_journey_at = last_journey_by_order.get(row["id"])
            if last_activity_at is None:
                last_activity_at = last_journey_at
            elif last_journey_at is not None and last_journey_at > last_activity_at:
                last_activity_at = last_journey_at

            action = derive_action_needed(
                row["assessment__sales_status_code"],
                row["status"],
                row["customer__is_new_customer"],
            )
            pending = last_activity_at is None or last_activity_at < row["created_at"]
            operation = operation_payload(
                operation_state_by_customer.get(row["customer_id"]), now=now
            )
            waiting_since, waiting_basis = ContactListService._waiting_since_for(
                row, last_activity_at=last_activity_at, gtc_at=gtc_at_by_order.get(row["id"])
            )
            enriched_rows.append(
                {
                    **row,
                    "action_needed": action,
                    "pending": pending,
                    "last_activity_at": last_activity_at,
                    "operation": operation,
                    # Tính ở đây (trên TOÀN tập, không chỉ trang) vì đây là khóa sắp xếp.
                    "waiting_since": waiting_since,
                    "waiting_basis": waiting_basis,
                }
            )
        return enriched_rows

    @staticmethod
    def _waiting_since_for(row: dict, *, last_activity_at, gtc_at) -> tuple:
        """→ (waiting_since, waiting_basis) cho một dòng đơn mới nhất.

        Nhóm chăm sóc sau bán đếm từ lúc khách NHẬN được hàng, không phải từ ngày
        data về — mốc GTC là thứ CSKH thực sự trễ so với nó. Đơn GTC trước
        23/07/2026 chưa có journey event → updated_at là mốc gần đúng nhất (cùng
        quy ước trang Theo dõi mua lại), nhưng đó chỉ là ước lượng: updated_at bị
        đẩy bởi BẤT Kỳ thao tác ghi nào lên đơn (gán lại sale, sync vận chuyển,
        sửa ghi chú). basis phải phân biệt hai trường hợp để FE không khẳng định
        sai một mốc chính xác. Đơn chưa GTC: chờ từ tương tác gần nhất, hoặc từ
        ngày data về nếu chưa tương tác."""
        if row["status"] == OrderStatus.DELIVERED_SUCCESS:
            if gtc_at is not None:
                return gtc_at, "gtc"
            return row["updated_at"], "gtc_estimated"
        waiting_since = row["created_at"]
        if last_activity_at is not None and last_activity_at > waiting_since:
            waiting_since = last_activity_at
        return waiting_since, "activity"

    @staticmethod
    def _base_queryset(*, user, date_from, date_to, q):
        base = Order.objects.filter(deleted_at__isnull=True, customer_id__isnull=False).exclude(
            order_source__in=_EXCLUDED_ORDER_SOURCES
        )

        base = _apply_contacts_scope(base, user=user)

        # Lọc theo khoảng timestamp thay vì `created_at__date` (cast `::date` lên cột
        # làm mọi index trên created_at vô dụng → Parallel Seq Scan toàn bảng, xem
        # EXPLAIN 2026-07-16). Chuyển sang nửa-mở [00:00 ngày from, 00:00 ngày to+1)
        # theo giờ địa phương — ngữ nghĩa y hệt __date__gte/__lte nhưng dùng được
        # index `ord_act_cr_desc_idx` (created_at DESC WHERE deleted_at IS NULL).
        if date_from is not None:
            start = timezone.make_aware(datetime.combine(date_from, time.min))
            base = base.filter(created_at__gte=start)
        if date_to is not None:
            end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min))
            base = base.filter(created_at__lt=end)

        if q:
            # `__ilike_contains` thay `icontains` (audit 2026-09-23 C1): ba cột đều có
            # GIN trigram bare-column trên `customers` (`cust_fullname_trgm_idx`,
            # `cust_phone_norm_trgm_idx`, `cust_phone_raw_trgm_idx`); `icontains`
            # sinh `UPPER(col) LIKE UPPER(%s)` → planner bỏ index, seq-scan
            # (apps/common/db_lookups.py).
            base = base.filter(
                Q(customer__full_name__ilike_contains=q)
                | Q(customer__phone_normalized__ilike_contains=q)
                | Q(customer__phone_raw__ilike_contains=q)
            )

        return base

    @staticmethod
    def _latest_order_rows(base) -> list[dict]:
        """Đơn mới nhất mỗi khách trong `base`. Materialize thành list Python — tránh
        lazy subquery bị dùng lại ở bước sau (bài học seq-scan order search perf)."""
        queryset = (
            base.order_by("customer_id", "-created_at", "-id")
            .distinct("customer_id")
            .values(
                "id",
                "customer_id",
                "created_at",
                "updated_at",
                "status",
                "assessment__sales_status_code",
                "customer__is_new_customer",
            )
        )
        return list(queryset)

    @staticmethod
    def _last_activity_map(customer_ids: list[int]) -> dict[int, object]:
        if not customer_ids:
            return {}
        rows = (
            ContactActivity.objects.filter(customer_id__in=customer_ids)
            .values("customer_id")
            .annotate(last=Max("created_at"))
        )
        return {row["customer_id"]: row["last"] for row in rows}

    @staticmethod
    def _last_journey_event_map(order_ids: list[int]) -> dict[int, object]:
        if not order_ids:
            return {}
        rows = (
            OrderJourneyEvent.objects.filter(
                order_id__in=order_ids, event_type__in=_JOURNEY_ACTIVITY_EVENT_TYPES
            )
            .values("order_id")
            .annotate(last=Max("created_at"))
        )
        return {row["order_id"]: row["last"] for row in rows}

    @staticmethod
    def _gtc_at_map(order_ids: list[int]) -> dict[int, object]:
        """order_id → mốc chuyển sang GTC GẦN NHẤT.

        Lấy Max chứ không Min: đơn có thể GTC → hoàn → GTC lại, bộ đếm chăm sóc
        sau bán phải tính từ lần GTC đang hiệu lực. Chạy trên các đơn GTC của TOÀN
        tập kết quả (cùng cỡ với `_last_journey_event_map`) vì mốc chờ là khóa sắp
        xếp. Index ord_journey_ord_type_cr_idx (order, event_type, created_at) phủ
        phần order_id + event_type; to_code là residual filter.
        """
        if not order_ids:
            return {}
        rows = (
            OrderJourneyEvent.objects.filter(
                order_id__in=order_ids,
                event_type=STATUS_CHANGED_EVENT,
                to_code=_GTC_TO_CODE,
            )
            .values("order_id")
            .annotate(last=Max("created_at"))
        )
        return {row["order_id"]: row["last"] for row in rows}

    @staticmethod
    def _order_items_map(order_ids: list[int]) -> dict[int, list[dict]]:
        """order_id → danh sách dòng hàng. 1 query trên index order_item_order_cr_idx."""
        if not order_ids:
            return {}
        result: dict[int, list[dict]] = {}
        rows = (
            OrderItem.objects.filter(order_id__in=order_ids)
            .order_by("order_id", "id")
            .values("order_id", "sku", "product_name", "quantity")
        )
        for row in rows:
            result.setdefault(row["order_id"], []).append(
                {
                    "sku": row["sku"],
                    "product_name": row["product_name"],
                    "quantity": row["quantity"],
                }
            )
        return result

    @staticmethod
    def _compute_action_counts(enriched_rows: list[dict]) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        all_total = 0
        all_pending = 0
        for row in enriched_rows:
            key = row["action_needed"]
            bucket = counts.setdefault(key, {"pending": 0, "total": 0})
            bucket["total"] += 1
            if row["pending"]:
                bucket["pending"] += 1

            all_total += 1
            if row["pending"] and key != "none":
                all_pending += 1

        counts["all"] = {"pending": all_pending, "total": all_total}
        return counts


    @staticmethod
    def _apply_scope_filters(enriched_rows: list[dict], *, interaction, customer_type) -> list[dict]:
        """Áp interaction + customer_type — KHÔNG áp action_needed. Dùng làm tập nền
        cho cả action_counts (chips) lẫn bước lọc action_needed tiếp theo."""
        rows = enriched_rows
        if interaction == "none":
            rows = [r for r in rows if r["pending"]]
        elif interaction == "recent":
            rows = [r for r in rows if not r["pending"]]
        if customer_type == "new":
            rows = [r for r in rows if r["customer__is_new_customer"]]
        elif customer_type == "old":
            rows = [r for r in rows if not r["customer__is_new_customer"]]
        return rows

    @staticmethod
    def _apply_action_needed_filter(rows: list[dict], *, action_needed) -> list[dict]:
        if not action_needed:
            return rows
        action_set = set(action_needed)
        return [r for r in rows if r["action_needed"] in action_set]

    @staticmethod
    def _build_items(page_rows: list[dict], *, user) -> list[dict]:
        if not page_rows:
            return []

        page_ids = [row["id"] for row in page_rows]
        orders_by_id = {
            order.id: order
            for order in Order.objects.filter(id__in=page_ids).select_related(
                "customer", "assessment", "channel", "staff"
            )
        }
        page_customer_ids = [row["customer_id"] for row in page_rows]
        # Che SĐT khách của contact KHÔNG do mình phụ trách (thiếu `customer-phone:view`).
        # "Của mình" xét theo sale của ĐƠN MỚI NHẤT — đúng dòng mà trang này hiển thị,
        # và cũng là đơn quyết định mọi cột tác nghiệp còn lại của dòng đó.
        # `_build_items` là đường đi chung của CẢ màn hình lẫn file Excel
        # (`iter_contact_batches`), nên che ở đây là che cả hai.
        visibility = phone_visibility_for(user)
        order_count_map = ContactListService._order_count_map(page_customer_ids, user=user)
        items_by_order = ContactListService._order_items_map(page_ids)

        items = []
        for row in page_rows:
            order = orders_by_id[row["id"]]
            customer = order.customer
            assessment = getattr(order, "assessment", None)
            staff = order.staff
            # waiting_since/basis đã tính trong `_compute_enriched_rows` (khóa sắp xếp).
            waiting_since = row["waiting_since"]
            waiting_basis = row["waiting_basis"]

            items.append(
                {
                    "customer_id": customer.id,
                    "customer_code": customer.code,
                    "full_name": customer.full_name,
                    "phone_raw": visibility.apply(
                        customer.phone_raw,
                        order.staff_id,
                        public_source=is_public_phone_source(order.order_source),
                    ),
                    "is_new_customer": customer.is_new_customer,
                    "latest_order": {
                        "id": order.id,
                        "created_at": order.created_at,
                        "order_source": order.order_source,
                        "channel_id": order.channel_id,
                        "channel_name": order.channel.name if order.channel_id else "",
                        # Địa chỉ đã chuẩn hoá của đơn mới nhất (chuỗi đầy đủ số nhà →
                        # tỉnh); đơn chưa chuẩn hoá rơi về chuỗi thô khách nhập.
                        "address": order.address or order.raw_address or "",
                        "status": order.status,
                        "items": items_by_order.get(order.id) or _items_from_sku_string(order.sku),
                        "total_after_discount": str(order.total_after_discount),
                        "sales_status_code": assessment.sales_status_code if assessment else None,
                        "next_followup_at": assessment.next_followup_at if assessment else None,
                        "staff_id": order.staff_id,
                        "staff_name": (staff.display_name or staff.username) if staff else "",
                    },
                    "order_count": order_count_map.get(customer.id, 0),
                    "action_needed": row["action_needed"],
                    "waiting_since": waiting_since if row["action_needed"] != "none" else None,
                    "waiting_basis": waiting_basis,
                    "last_activity_at": row["last_activity_at"],
                    "operation": row["operation"],
                }
            )
        return items

    @staticmethod
    def _order_count_map(customer_ids: list[int], *, user) -> dict[int, int]:
        """Tổng số đơn (luồng sale landing, không tính đơn sàn) của các khách TRÊN
        TRANG hiện tại — 1 query aggregate, không chạy trên toàn tập kết quả.
        Áp cùng RBAC scope (đủ 4 mức) với danh sách."""
        if not customer_ids:
            return {}
        queryset = Order.objects.filter(
            deleted_at__isnull=True, customer_id__in=customer_ids
        ).exclude(order_source__in=_EXCLUDED_ORDER_SOURCES)
        queryset = _apply_contacts_scope(queryset, user=user)
        rows = queryset.values("customer_id").annotate(count=Count("id"))
        return {row["customer_id"]: row["count"] for row in rows}
