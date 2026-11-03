"""Các bộ lọc CỜ / QUAN HỆ / TRẠNG THÁI ASSESSMENT / KHOẢNG NGÀY của danh sách
đơn — các nhánh cuối của ``apply_list_filters`` (list_queries.py), giữ ĐÚNG thứ
tự áp như bản gốc.

Tách nguyên trạng từ ``OrderRepository._apply_list_filters`` /
``_apply_assessment_status_filter`` (audit T3/P2-3) — KHÔNG đổi hành vi, chỉ dời chỗ.
"""
from datetime import date

from django.db.models import Exists, OuterRef, Q

from apps.orders.models import OrderAssessment, OrderRecall, OrderRecallEntry

from .base import parse_local_datetime_boundary


def apply_assessment_status_filter(queryset, values, field: str):
    """Lọc đơn theo một status code của OrderAssessment (`field`), hỗ trợ giá trị
    đặc biệt "__empty__" (đơn KHÔNG có assessment, hoặc có nhưng code null/rỗng).

    Nhánh "__empty__" được biểu diễn bằng ``~Exists(assessment có code cụ thể)``
    thay vì ``Q(assessment__isnull=True) | ...``. Dạng OR-with-isnull ép Postgres
    chuyển sang Hash Right Join quét TOÀN BỘ bảng order_assessments (~470k dòng,
    ~1s trong EXPLAIN). ~Exists để planner drive từ tập đơn (đã lọc, nhỏ) rồi probe
    `order_assessments` qua unique index `order_id` → anti-join theo từng đơn.
    Nhánh chọn code cụ thể (không kèm __empty__) giữ nguyên INNER JOIN + index đơn
    cột (ord_assessment_sales_idx / ord_assessment_delivery_idx) vốn đã nhanh.
    """
    if not values:
        return queryset
    values = list(values) if isinstance(values, list) else [values]
    include_empty = "__empty__" in values
    concrete = [v for v in values if v != "__empty__"]
    if not include_empty:
        # Semi-join qua Exists để planner drive từ tập đơn (đã lọc, nhỏ) rồi probe
        # order_assessments qua unique index order_id — thay vì INNER JOIN mà planner
        # seq-scan order_assessments khi code phổ biến (vd 'created_order' ~33% tổng
        # assessment → ~190ms). Nested Loop semi-join theo order_id ~vài chục ms.
        return queryset.filter(
            Exists(
                OrderAssessment.objects.filter(
                    order_id=OuterRef("pk"), **{f"{field}__in": concrete}
                )
            )
        )
    # "other" = assessment có code cụ thể (không null/rỗng) NẰM NGOÀI tập `concrete`.
    # Đơn chỉ có tối đa 1 assessment (OneToOne) nên:
    #   - đơn empty (không assessment / code rỗng) -> không có "other" -> giữ lại
    #   - đơn có code thuộc `concrete` -> không có "other" -> giữ lại
    #   - đơn có code ngoài `concrete` -> có "other" -> loại
    # => "empty OR code ∈ concrete" tương đương ~Exists(other).
    other = (
        OrderAssessment.objects.filter(order_id=OuterRef("pk"))
        .exclude(**{f"{field}__isnull": True})
        .exclude(**{field: ""})
    )
    if concrete:
        other = other.exclude(**{f"{field}__in": concrete})
    return queryset.filter(~Exists(other))


def apply_sales_status_filter(queryset, sales_status):
    return apply_assessment_status_filter(queryset, sales_status, "sales_status_code")


def apply_flag_filters(queryset, filters: dict):
    """has_shipping_code → has_invoice_code → is_delivery_invoice_printed →
    customer_return → has_address (cột ngay trên Order)."""
    has_shipping_code = str(filters.get("has_shipping_code") or "").strip()
    if has_shipping_code == "yes":
        queryset = queryset.exclude(shipping_code="").exclude(shipping_code__isnull=True)
    elif has_shipping_code == "no":
        queryset = queryset.filter(Q(shipping_code="") | Q(shipping_code__isnull=True))
    has_invoice_code = str(filters.get("has_invoice_code") or "").strip()
    if has_invoice_code == "yes":
        queryset = queryset.exclude(invoice_code="").exclude(invoice_code__isnull=True)
    elif has_invoice_code == "no":
        queryset = queryset.filter(Q(invoice_code="") | Q(invoice_code__isnull=True))
    is_delivery_invoice_printed = str(filters.get("is_delivery_invoice_printed") or "").strip()
    if is_delivery_invoice_printed == "yes":
        queryset = queryset.filter(is_delivery_invoice_printed=True)
    elif is_delivery_invoice_printed == "no":
        queryset = queryset.filter(is_delivery_invoice_printed=False)
    customer_return = str(filters.get("customer_return") or "").strip()
    if customer_return == "yes":
        queryset = queryset.filter(customer_return=True)
    elif customer_return == "no":
        queryset = queryset.filter(customer_return=False)
    # Cờ bất biến "đơn ingest về có địa chỉ" (không phản ánh địa chỉ hiện tại).
    has_address = str(filters.get("has_address") or "").strip()
    if has_address == "yes":
        queryset = queryset.filter(has_address=True)
    elif has_address == "no":
        queryset = queryset.filter(has_address=False)
    return queryset


def _apply_yes_no_exists(queryset, filters: dict, key: str, exists):
    """Áp `Exists` (yes) / `~Exists` (no) theo giá trị "yes"/"no" của `filters[key]`."""
    value = str(filters.get(key) or "").strip()
    if value == "yes":
        return queryset.filter(exists)
    if value == "no":
        return queryset.filter(~exists)
    return queryset


def apply_relation_filters(queryset, filters: dict):
    """has_internal_note → has_return_slip → has_recall_code → has_call_recording
    (semi-/anti-join qua Exists trên bảng liên quan)."""
    # Ghi chú nội bộ nằm trên OrderAssessment (relation `assessment`), không phải
    # trên Order. Dùng semi-/anti-join qua Exists để planner drive từ tập đơn (đã
    # lọc, nhỏ) rồi probe order_assessments qua unique index order_id — tránh
    # Hash Right Join quét toàn bộ bảng assessment (xem apply_assessment_status_filter).
    if str(filters.get("has_internal_note") or "").strip() in ("yes", "no"):
        note_exists = Exists(
            OrderAssessment.objects.filter(order_id=OuterRef("pk")).exclude(internal_note="")
        )
        queryset = _apply_yes_no_exists(queryset, filters, "has_internal_note", note_exists)
    # "Phiếu trả hàng" nằm trên OrderRecall (relation `recall`, 1-1) — cùng
    # pattern Exists như has_internal_note: drive từ tập đơn đã lọc, probe
    # order_recalls qua unique index order_id.
    if str(filters.get("has_return_slip") or "").strip() in ("yes", "no"):
        slip_exists = Exists(
            OrderRecall.objects.filter(order_id=OuterRef("pk"), return_slip_code__len__gt=0)
        )
        queryset = _apply_yes_no_exists(queryset, filters, "has_return_slip", slip_exists)
    # "Mã đơn thu hồi" nằm trên OrderRecallEntry (relation `recall_entries`, 1-N,
    # Task 1 tách khỏi OrderRecall) — cùng pattern Exists như has_return_slip:
    # drive từ tập đơn đã lọc, probe order_recall_entries qua FK order_id.
    if str(filters.get("has_recall_code") or "").strip() in ("yes", "no"):
        recall_exists = Exists(
            OrderRecallEntry.objects.filter(order_id=OuterRef("pk")).exclude(recall_code="")
        )
        queryset = _apply_yes_no_exists(queryset, filters, "has_recall_code", recall_exists)
    # "Ghi âm cuộc gọi" nằm trên callcenter_call_records (N-1 về Order) — cùng
    # pattern Exists: probe qua index callrec_order_start_idx, chỉ tính cuộc
    # gọi có file ghi âm (recording_url rỗng = gọi nhỡ, không tính).
    if str(filters.get("has_call_recording") or "").strip() in ("yes", "no"):
        # Import cục bộ: orders là app lõi, không kéo dependency callcenter ở
        # module level (callcenter.sync_service đã import ngược orders.models).
        from apps.callcenter.models import CallRecord

        recording_exists = Exists(
            CallRecord.objects.filter(order_id=OuterRef("pk")).exclude(recording_url="")
        )
        queryset = _apply_yes_no_exists(queryset, filters, "has_call_recording", recording_exists)
    return queryset


def apply_assessment_status_filters(queryset, filters: dict):
    """delivery_business_status → sales_status."""
    delivery_business_status = filters.get("delivery_business_status")
    if delivery_business_status:
        queryset = apply_assessment_status_filter(
            queryset, delivery_business_status, "delivery_business_status_code"
        )
    return apply_sales_status_filter(queryset, filters.get("sales_status"))


def _created_at_q(filters: dict) -> Q | None:
    created_q = Q()
    has_created = False
    date_from = filters.get("date_from")
    if date_from:
        from_dt = parse_local_datetime_boundary(date_from)
        if from_dt is not None:
            created_q &= Q(created_at__gte=from_dt)
            has_created = True
    date_to = filters.get("date_to")
    if date_to:
        to_dt_exclusive = parse_local_datetime_boundary(date_to, end_exclusive=True)
        if to_dt_exclusive is not None:
            created_q &= Q(created_at__lt=to_dt_exclusive)
            has_created = True
    return created_q if has_created else None


def _return_date_q(filters: dict) -> Q | None:
    return_q = Q()
    has_return = False
    return_date_from = filters.get("return_date_from")
    if return_date_from:
        try:
            return_q &= Q(return_date__gte=date.fromisoformat(return_date_from))
            has_return = True
        except ValueError:
            pass
    return_date_to = filters.get("return_date_to")
    if return_date_to:
        try:
            return_q &= Q(return_date__lte=date.fromisoformat(return_date_to))
            has_return = True
        except ValueError:
            pass
    return return_q if has_return else None


def _kiotviet_purchase_date_q(filters: dict) -> Q | None:
    # "Ngày tạo trên KiotViet" (kiotviet_purchase_date, DateTimeField) — biên
    # ngày local như date_from/date_to (to = hết ngày, exclusive). KHÔNG bị
    # tắt khi search (q): giống return_date, người dùng chủ động nhập mới có.
    # Đơn chưa có hoá đơn KiotViet (NULL) rơi ra ngoài khi bật lọc — đúng ý,
    # đây là bộ lọc theo mốc KiotViet chứ không phải mốc hệ thống.
    # PHẢI khai key ở CẢ NĂM dict filter của các list endpoint (xem
    # build_orders_filters) — thiếu một chỗ là bộ lọc im lặng không chạy ở đó.
    kv_purchase_q = Q()
    has_kv_purchase = False
    kv_purchase_from = filters.get("kiotviet_purchase_date_from")
    if kv_purchase_from:
        from_dt = parse_local_datetime_boundary(kv_purchase_from)
        if from_dt is not None:
            kv_purchase_q &= Q(kiotviet_purchase_date__gte=from_dt)
            has_kv_purchase = True
    kv_purchase_to = filters.get("kiotviet_purchase_date_to")
    if kv_purchase_to:
        to_dt_exclusive = parse_local_datetime_boundary(kv_purchase_to, end_exclusive=True)
        if to_dt_exclusive is not None:
            kv_purchase_q &= Q(kiotviet_purchase_date__lt=to_dt_exclusive)
            has_kv_purchase = True
    return kv_purchase_q if has_kv_purchase else None


def apply_date_range_filters(queryset, filters: dict, raw_q: str):
    """Khoảng ngày tạo (bỏ qua khi search) → ngày hoàn hàng → ngày tạo KiotViet.

    Khi đang tìm kiếm (q), bỏ qua lọc ngày — giống như show_orders — để search
    theo SĐT/mã đơn/mã vận đơn luôn quét toàn bộ lịch sử, không bị bó trong khoảng
    ngày mặc định (this_month) của trang. Nếu không, đơn tạo ngoài tháng hiện tại
    (hoặc có created_at lỗi) sẽ "có trong DB mà search không ra".
    Lọc theo ngày tạo (created_at) và ngày hoàn hàng (return_date) kết hợp bằng
    AND: mỗi khoảng được áp độc lập, khi người dùng nhập cả hai khoảng đơn phải
    thỏa ĐỒNG THỜI cả hai. Khi chỉ nhập một khoảng, chỉ lọc theo khoảng đó (ví dụ
    chỉ nhập ngày hoàn hàng -> chỉ lọc theo return_date).
    """
    created_q = None if raw_q else _created_at_q(filters)
    return_q = _return_date_q(filters)
    kv_purchase_q = _kiotviet_purchase_date_q(filters)

    if created_q is not None:
        queryset = queryset.filter(created_q)
    if return_q is not None:
        queryset = queryset.filter(return_q)
    if kv_purchase_q is not None:
        queryset = queryset.filter(kv_purchase_q)
    return queryset
