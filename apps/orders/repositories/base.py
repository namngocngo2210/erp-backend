"""Helper nền dùng chung cho mọi truy vấn đơn hàng của ``OrderRepository``.

Tách nguyên trạng từ ``apps/orders/repositories.py`` (audit T3/P2-3) — KHÔNG đổi
hành vi, chỉ dời chỗ. Các helper ở đây được cả mixin list/export/kiotviet-job
(cùng package) lẫn các mixin báo cáo (``apps.orders.reporting``) gọi qua ``cls.``.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Exists, OuterRef, Prefetch, Subquery
from django.utils import timezone

from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.orders.models import (
    Order,
    OrderJourneyEvent,
    OrderKiotVietJob,
    OrderKiotVietJobStatus,
    OrderNeed,
)
from apps.orders.reporting import _ads_excluded_channel_ids, sale_report_channel_ids
from apps.users.constants import DepartmentID

# Upper bound on how many matched order ids a `q` search materializes before the page
# fetch. Real "find this order" searches resolve to a handful of rows; this only bites a
# degenerate broad substring match, keeping the fetch index-scannable (sub-100ms) instead
# of letting a multi-thousand IN-list flip Postgres back to a full seq-scan + join.
SEARCH_MATCH_ID_CAP = 500

# Hard cap on how many rows a single filter-based Excel export may materialize. The export
# builds the whole workbook in memory (openpyxl), so an unbounded "export everything" would
# OOM the worker. A sales reconciliation export is a few thousand rows at most; beyond this
# the caller is asked to narrow the filter instead of silently truncating.
EXPORT_MAX_ROWS = 50000

# Quy tắc "đơn đã hủy" DUY NHẤT của module: CANCELLED_ORDER_Q — chuyển định nghĩa
# sang reporting/shared.py (summary_overview dùng chung, tránh import vòng);
# package `__init__` import lại giữ tương thích cho các usage nội bộ.


def parse_local_datetime_boundary(value: str, *, end_exclusive: bool = False):
    """Parse ``YYYY-MM-DD`` (hoặc ISO datetime) thành datetime aware theo TZ hiện tại.

    ``end_exclusive=True``: cộng thêm 1 ngày để dùng làm biên ``__lt`` (hết ngày).
    Trả None khi chuỗi rỗng/không parse được. Hàm module-level để các hàm lọc thuần
    (list_filter_*.py) gọi thẳng; ``OrderRepository._parse_local_datetime_boundary``
    là alias staticmethod giữ API cũ.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            base = datetime.combine(datetime.fromisoformat(raw).date(), time.min)
            if end_exclusive:
                base += timedelta(days=1)
        else:
            base = datetime.fromisoformat(raw)
            if end_exclusive:
                base += timedelta(days=1)
        if timezone.is_naive(base):
            base = timezone.make_aware(base, timezone.get_current_timezone())
        return base
    except ValueError:
        return None


class OrderQuerysetBaseMixin:
    """Helper nền: base queryset, join/prefetch CRM, KiotViet job, cache count."""

    _parse_local_datetime_boundary = staticmethod(parse_local_datetime_boundary)

    @classmethod
    def ads_channel_scope(cls) -> tuple[list[int], set[int]]:
        """Scope kênh dùng chung cho dashboard/ads report (audit follow-up mục 3).

        Trả về ``(sale_channel_ids, excluded_ids)`` — public entry point cho
        transport layer, tránh phải import trực tiếp module private
        ``apps.orders.reporting.shared``.
        """
        return sale_report_channel_ids(), _ads_excluded_channel_ids()

    @staticmethod
    def _exclude_hidden_branches(queryset):
        return queryset.exclude(branch_id__in=EXCLUDED_BRANCH_IDS)

    @staticmethod
    def _apply_created_at_date_filters(queryset, date_from: str | None = None, date_to: str | None = None):
        if date_from:
            from_dt = parse_local_datetime_boundary(date_from)
            if from_dt is not None:
                queryset = queryset.filter(created_at__gte=from_dt)
        if date_to:
            to_dt_exclusive = parse_local_datetime_boundary(date_to, end_exclusive=True)
            if to_dt_exclusive is not None:
                queryset = queryset.filter(created_at__lt=to_dt_exclusive)
        return queryset

    @staticmethod
    def _with_related_crm(queryset, *, with_journey_events: bool = True, lean_list_columns: bool = False):
        """`lean_list_columns=True` (các trang list dùng OrderListSerializer/
        OrderCRMListSerializer): bỏ join assessment__updated_by (updated_by_name đã
        cắt khỏi crm list) và defer các cột NẶNG mà list không serialize — 3 JSON
        blob của assessment + các cột text dài của Order (nằm trong
        LIST_EXCLUDED_ORDER_FIELDS). rows=1 mà page vẫn 66-160ms (list-timing
        2026-08-04 14h) phần lớn vì SELECT kéo full cột của 7 bảng join.
        Sheet3/detail/history giữ mặc định — crm đầy đủ cần các cột này, đụng
        field bị defer là Django tự query lại per-row (N+1)."""
        select_related_fields = [
            "customer",
            "channel",
            # channel_type đi kèm channel: OrderListSerializer đọc
            # channel.channel_type.code trên MỌI dòng, thiếu join là N+1.
            "channel__channel_type",
            "kiotviet_channel",
            "staff",
            "assessment",
            "recall",
            "recall__updated_by",
        ]
        if not lean_list_columns:
            select_related_fields.append("assessment__updated_by")
        qs = queryset.select_related(*select_related_fields).prefetch_related(
            "items",
            "recall_entries",
            Prefetch("assessment__needs", queryset=OrderNeed.objects.select_related("need_type__group").order_by("id")),
        )
        if lean_list_columns:
            qs = qs.defer(
                "assessment__ai_open_insights",
                "assessment__extra_fields",
                "assessment__ai_filled_fields",
                "product_raw",
                "feedback",
                "result",
                "url_page",
                "ward",
                "district",
                "province",
                "ward_code",
                "district_code",
                "province_code",
            )
        # The journey-events prefetch is heavy (full timeline + actor join). List endpoints
        # don't render the timeline, so they opt out; detail / history endpoints keep it on.
        if with_journey_events:
            qs = qs.prefetch_related(
                Prefetch(
                    "journey_events",
                    queryset=OrderJourneyEvent.objects.select_related("actor").order_by("-created_at", "-id"),
                ),
            )
        return qs

    @staticmethod
    def _active_kiotviet_job_subquery():
        timeout_seconds = int(getattr(settings, "KIOTVIET_JOB_ACTIVE_TIMEOUT_SECONDS", 300))
        cutoff = timezone.now() - timedelta(seconds=max(0, timeout_seconds))
        return OrderKiotVietJob.objects.filter(
            order_id=OuterRef("pk"),
            status__in=[OrderKiotVietJobStatus.PENDING, OrderKiotVietJobStatus.RUNNING],
            created_at__gte=cutoff,
        )

    @staticmethod
    def kiotviet_job_maps(order_ids):
        """Batch-load KiotViet job state for a page of orders in 2 queries, replacing the
        4 per-row Subquery/Exists annotations the list query used to carry. Returns
        (latest_by_order_id, active_order_ids). Consumed by _build_order_list_context."""
        ids = list(order_ids)
        if not ids:
            return {}, set()
        latest_by_order = {
            row["order_id"]: row
            for row in (
                OrderKiotVietJob.objects.filter(order_id__in=ids)
                .order_by("order_id", "-created_at", "-id")
                .distinct("order_id")
                .values("order_id", "status", "action", "error_message", "requested_by_id")
            )
        }
        timeout_seconds = int(getattr(settings, "KIOTVIET_JOB_ACTIVE_TIMEOUT_SECONDS", 300))
        cutoff = timezone.now() - timedelta(seconds=max(0, timeout_seconds))
        active_order_ids = set(
            OrderKiotVietJob.objects.filter(
                order_id__in=ids,
                status__in=[OrderKiotVietJobStatus.PENDING, OrderKiotVietJobStatus.RUNNING],
                created_at__gte=cutoff,
            ).values_list("order_id", flat=True)
        )
        return latest_by_order, active_order_ids

    @classmethod
    def _base_orders_queryset(cls, *, include_cancelled: bool = False):
        qs = Order.objects.filter(deleted_at__isnull=True)
        if not include_cancelled:
            qs = qs.exclude(status=6).exclude(invoice_status=2)
        return cls._exclude_hidden_branches(qs)

    @classmethod
    def _sales_report_orders_queryset(cls, *, sale_channel_ids: list[int] | None = None):
        # Phạm vi kênh = kênh CÓ gán NV phòng Ads (sale_report_channel_ids), thay cho
        # whitelist id cứng cũ — tự cập nhật khi gán/gỡ NV Ads, không lệch khi mở kênh
        # mới. NOTE: bỏ filter staff__role=STAFF cũ; staff__department_id=SALES đã đủ
        # phân định nhân viên sale (admin không gán vào phòng Sales).
        # sale_channel_ids: truyền sẵn khi caller (dashboard_summary) đã tính 1 lần
        # cho cả request (audit 2026-07 follow-up task 11); None -> tự tính.
        if sale_channel_ids is None:
            sale_channel_ids = sale_report_channel_ids()
        return cls._base_orders_queryset().filter(
            channel_id__in=sale_channel_ids,
            staff__department_id=DepartmentID.SALES,
            staff__deleted_at__isnull=True,
        )

    @classmethod
    def _wrap_with_related_and_annotations(cls, queryset, *, with_journey_events: bool = True):
        latest_job_subquery = OrderKiotVietJob.objects.filter(order_id=OuterRef("pk")).order_by("-created_at", "-id")
        return cls._with_related_crm(queryset, with_journey_events=with_journey_events).annotate(
            is_kiotviet_processing=Exists(cls._active_kiotviet_job_subquery()),
            latest_kiotviet_job_status=Subquery(latest_job_subquery.values("status")[:1]),
            latest_kiotviet_job_action=Subquery(latest_job_subquery.values("action")[:1]),
            latest_kiotviet_job_error=Subquery(latest_job_subquery.values("error_message")[:1]),
            latest_kiotviet_job_requested_by=Subquery(latest_job_subquery.values("requested_by_id")[:1]),
        )

    @staticmethod
    def _filters_cache_key(prefix: str, filters: dict) -> str | None:
        """Khóa cache theo hash bộ lọc, dùng chung cho count/status_counts. Bỏ
        limit/offset/sort khỏi khóa vì chúng không đổi tập đếm. `filters` đã gồm
        scope user (owner_user_id / channel) nên khóa tự tách theo scope. Trả None
        khi bộ lọc chứa giá trị không JSON-hóa được — caller bỏ cache, tính thẳng."""
        import hashlib
        import json

        key_src = {k: v for k, v in filters.items() if k not in ("limit", "offset", "sort")}
        try:
            raw = json.dumps(key_src, sort_keys=True, default=str)
        except TypeError:
            return None
        return prefix + hashlib.md5(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _count_with_cache(cls, base, filters: dict) -> int:
        """COUNT tập khớp bộ lọc, có cache ngắn hạn theo hash bộ lọc (khi bật qua
        settings.ORDERS_LIST_COUNT_CACHE_TTL > 0). Lật trang / re-fetch cùng bộ lọc
        sẽ tái dùng count thay vì quét lại toàn tập. Số đếm có thể trễ tối đa TTL giây
        so với đơn mới tạo/xoá — chấp nhận được cho nhãn "tổng N đơn"; danh sách vẫn
        realtime.
        """
        from django.conf import settings

        ttl = int(getattr(settings, "ORDERS_LIST_COUNT_CACHE_TTL", 0) or 0)
        if ttl <= 0:
            return base.count()

        from django.core.cache import cache

        cache_key = cls._filters_cache_key("orders:list_count:", filters)
        if cache_key is None:
            return base.count()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        total = base.count()
        cache.set(cache_key, total, ttl)
        return total
