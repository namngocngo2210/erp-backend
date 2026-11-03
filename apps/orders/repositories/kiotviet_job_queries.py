"""Danh sách job đồng bộ KiotViet (``OrderKiotVietJob``).

Tách nguyên trạng từ ``apps/orders/repositories.py`` (audit T3/P2-3) — KHÔNG đổi
hành vi, chỉ dời chỗ.
"""
from django.db.models import Exists, OuterRef, Q

from apps.orders.models import Order, OrderKiotVietJob

from .base import SEARCH_MATCH_ID_CAP


def _kiotviet_job_search_condition(q: str) -> Q:
    """Điều kiện tìm kiếm `q` cho trang job — lọc trên `orders` TRƯỚC rồi `order_id__in`."""
    # Trước đây: 8 điều kiện `icontains` nối OR, 7 trong số đó XUYÊN JOIN
    # `order__…`. Hỏng cả hai lớp (audit 2026-07-26 C2):
    #   - `icontains` sinh `UPPER(col) LIKE UPPER(%s)` → planner bỏ trigram GIN
    #     bare-column trên các cột mã;
    #   - OR xuyên join thì Postgres không BitmapOr được, phải nối `orders` với
    #     `order_kiotviet_jobs` rồi mới lọc → seq-scan cả hai bảng mỗi lần gõ.
    # Cách sửa: lọc trên `orders` TRƯỚC bằng `__contains` (dùng được trigram),
    # thu id có chặn trên rồi `order_id__in` — đúng kỹ thuật `_apply_list_filters`
    # dùng cho trang danh sách đơn (xem comment quanh SEARCH_MATCH_ID_CAP).
    from apps.common.phone_utils import phone_search_candidates

    order_q = Q()
    if len(q) >= 3:
        # Ngưỡng 3 ký tự: trigram GIN không phục vụ được LIKE 1-2 ký tự (sẽ
        # seq-scan) và một mã 2 ký tự thì khớp gần như mọi đơn.
        order_q |= Q(order_code__contains=q)
        order_q |= Q(invoice_code__contains=q)
        order_q |= Q(old_invoice_code__contains=q)
        order_q |= Q(shipping_code__contains=q)
        # external_order_id: từ migration 0109 đã có trigram GIN
        # `order_ext_order_id_trgm_idx` (trước đó cột này không có index nào và
        # nhánh này buộc quét bảng). `__contains` là dạng dùng được index đó —
        # đổi sang `__icontains` sẽ quay lại seq-scan.
        order_q |= Q(external_order_id__contains=q)
        # Tên khách là NGOẠI LỆ được phép `icontains`: functional index
        # `order_name_upper_trgm_idx` phục vụ đúng biểu thức
        # `UPPER(name::text) LIKE UPPER(%s)`. Tên tiếng Việt cần case-insensitive.
        order_q |= Q(name__icontains=q)
    for candidate in phone_search_candidates(q):
        order_q |= Q(phone_normalized__contains=candidate)
        # Thêm cả cột SĐT THÔ: khi `phone` không chuẩn hóa được (ô nhập 2 số,
        # thiếu chữ số...) thì `phone_normalized` rỗng và đơn sẽ biến mất khỏi
        # tìm kiếm. `phone` cũng có trigram GIN bare-column (order_phone_trgm_idx)
        # nên nhánh này vẫn index-scan.
        order_q |= Q(phone__contains=candidate)

    # `error_message` là TextField của chính bảng job và KHÔNG có index nào, nên
    # `icontains` ở đây không làm mất index (bảng bị quét trong cả hai trường hợp);
    # giữ case-insensitive vì thông báo lỗi là văn xuôi tiếng Việt có hoa/thường.
    condition = Q(error_message__icontains=q) if len(q) >= 3 else Q()
    if order_q:
        matched_ids = list(
            Order.objects.filter(order_q)
            .order_by("-created_at")
            .values_list("id", flat=True)[:SEARCH_MATCH_ID_CAP]
        )
        condition |= Q(order_id__in=matched_ids)
    return condition


class OrderKiotVietJobQueriesMixin:
    @classmethod
    def list_kiotviet_jobs(cls, filters: dict, *, order_scope_q: Q | None = None):
        """Danh sách job đồng bộ KiotViet.

        ``order_scope_q`` là record-scope tính sẵn ở tầng view
        (`order_record_scope_q`) — Q trên bảng ``orders``. Repository chỉ áp, không tự
        resolve quyền (giữ tầng nghiệp vụ RBAC ở transport như các endpoint đơn khác).
        Quy ước giống `order_record_scope_q`: ``Q()`` rỗng = không giới hạn,
        ``Q(pk__in=[])`` = không khớp đơn nào.
        """
        queryset = OrderKiotVietJob.objects.select_related("order__channel", "requested_by").filter(order__deleted_at__isnull=True)

        if order_scope_q:
            # EXISTS tương quan thay vì `order_id__in=<subquery>`: với scope phòng ban
            # (vài trăm nghìn đơn) thì IN-subquery buộc Postgres vật chất hóa cả tập id,
            # còn EXISTS cho phép nối lồng theo khóa chính của từng job đang xét — hợp
            # với việc trang này chỉ lấy tối đa 100 dòng mới nhất.
            queryset = queryset.filter(
                Exists(Order.objects.filter(pk=OuterRef("order_id")).filter(order_scope_q))
            )

        action = filters.get("action")
        if action:
            queryset = queryset.filter(action=action)

        status = filters.get("status")
        if status:
            queryset = queryset.filter(status=status)

        requested_by = filters.get("requested_by")
        if requested_by:
            queryset = queryset.filter(requested_by_id=requested_by)

        channel = filters.get("channel")
        if channel:
            queryset = queryset.filter(order__channel__code=channel)

        order_id = filters.get("order_id")
        if order_id:
            queryset = queryset.filter(order_id=order_id)

        q = str(filters.get("q") or "").strip()
        if q:
            condition = _kiotviet_job_search_condition(q)
            queryset = queryset.filter(condition) if condition else queryset.none()

        date_from = filters.get("date_from")
        if date_from:
            from_dt = cls._parse_local_datetime_boundary(date_from)
            if from_dt is not None:
                queryset = queryset.filter(created_at__gte=from_dt)

        date_to = filters.get("date_to")
        if date_to:
            to_dt_exclusive = cls._parse_local_datetime_boundary(date_to, end_exclusive=True)
            if to_dt_exclusive is not None:
                queryset = queryset.filter(created_at__lt=to_dt_exclusive)

        limit = filters.get("limit") or 200
        try:
            parsed_limit = int(limit)
        except (TypeError, ValueError):
            parsed_limit = 200
        parsed_limit = max(1, min(parsed_limit, 100))
        return queryset.order_by("-created_at", "-id")[:parsed_limit]
