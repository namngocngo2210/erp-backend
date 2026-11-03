import logging
import time
from typing import TypedDict
from collections.abc import Callable, Iterable

from django.core.exceptions import ImproperlyConfigured
from django.db.models import F, Q
from rest_framework import status
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.common.phone_utils import normalize_phone
from apps.users.permissions import ORDER_SECTIONS
from apps.users.phone_visibility import PhoneVisibility, phone_visibility_for

from apps.orders.models import MARKETPLACE_ORDER_SOURCES, Order

logger = logging.getLogger(__name__)

# Re-export tương thích: `_apply_user_channel_scope` dời sang apps/orders/
# repositories.py ở Task 18 (business layer sở hữu, transport chỉ mượn lại) —
# xem docstring tại chỗ định nghĩa mới. Giữ import này để order_list_view.py /
# sheet3_view.py / order_export_view.py không phải đổi câu `from ._view_helpers
# import _apply_user_channel_scope` đang có.
from apps.orders.repositories import _apply_user_channel_scope  # noqa: F401

# Tập section dùng để resolve record-scope cho các endpoint GHI dùng chung cả 4 trang
# đơn (những endpoint guard bằng `require_section(*ORDER_SECTIONS)`: bulk-delete,
# restore-cancelled, cancel-kiotviet, kiotviet-actions, kship-*).
#
# Phải khớp ĐÚNG danh sách section mà endpoint truyền cho `require_section`: quyền vào
# được endpoint là "có BẤT KỲ section nào trong danh sách", nên record-scope cũng phải
# là HỢP của các section đó (xem `order_record_scope_q`).
ORDER_WRITE_SCOPE_SECTIONS = ORDER_SECTIONS

# Tập section dùng để resolve record-scope cho các endpoint đọc/ghi CHI TIẾT 1 đơn
# (`update_order_inline`, `get_order_journey_events`). Có thêm "shipments" vì user kho
# xem/cập nhật trạng thái giao hàng của mọi đơn: nếu chỉ resolve theo ORDER_SECTIONS họ
# rơi về scope mặc định OWN của section "orders" và mất quyền giữa luồng giao hàng.
#
# Ở `_view_helpers` (không phải trong từng view) vì đã có 2 view dùng chung — hằng nằm
# cạnh `order_record_scope_q` để người sửa scope thấy ngay mọi tập section đang tồn tại.
INLINE_SCOPE_SECTIONS = (*ORDER_SECTIONS, "shipments")

# Thông báo 403 khi user vào được endpoint (có ít nhất 1 section) nhưng bản ghi cụ thể
# nằm ngoài record-scope của mọi section họ được cấp.
ORDER_RECORD_FORBIDDEN_DETAIL = "Bạn không có quyền với đơn hàng này."
ORDER_NOT_FOUND_DETAIL = "Không tìm thấy đơn hàng."

# Điều kiện NHẬN DẠNG đơn của từng trang đơn — copy nguyên nghĩa từ forced_filters của
# các endpoint danh sách trong `order_list_view.py`, để record-scope của endpoint GHI/ĐỌC
# đơn lẻ mirror đúng tập dòng mà trang đó hiển thị.
#
# - "orders"        : list_orders KHÔNG khóa dòng nào → tập CHA, không điều kiện.
# - "shipments"     : list_shipment_orders có khóa `has_shipping_code=yes` +
#                     `sales_status=[created_order]`, nhưng đó là TRẠNG THÁI VẬN HÀNH
#                     BIẾN ĐỔI, không phải danh tính đơn. Gate ghi theo trạng thái biến
#                     đổi tự khoá chính nó: một PATCH làm mất điều kiện (đổi
#                     sales_status) sẽ chặn luôn PATCH kế tiếp của cùng user trên cùng
#                     đơn, giữa luồng giao hàng. Nên coi "shipments" là tập CHA.
# - "koc-orders"    : list_koc_orders khóa cứng `order_source="koc"` (order_list_view.py
#                     dòng 226).
# - "landing-orders": list_landing_orders khóa `order_source_present="true"` +
#                     `order_source_exclude=["koc", *MARKETPLACE_ORDER_SOURCES]`
#                     (dòng 326-329) → đơn CÓ nguồn, trừ koc và trừ đơn sàn TMĐT.
#                     `order_source=""` ở dòng 326 là giá trị rỗng nên repository bỏ qua
#                     (repositories.py:486-488), không phải điều kiện. Các nhánh
#                     `sheet=1/2` (dòng 343-348) chỉ là TAB do client chọn, không phải
#                     danh tính trang → không đưa vào đây.
# - "customers"    : hồ sơ 360 của khách (`customer_orders`) liệt kê MỌI đơn của khách
#                    đó, không khoá theo nguồn/kênh → tập CHA.
# - "warranty"     : luồng bảo hành (`search_orders`) phải tra được đơn của BẤT KỲ sale
#                    nào — khách mang máy tới bảo hành thì đơn gốc có thể của ai cũng
#                    được; bản thân section warranty (apps/warranty/claim_views.py) cũng
#                    không record-scope phiếu theo người tạo → tập CHA.
# - "resale-tracking": trang Theo dõi tái mua (`CustomerResaleService.list_resale_tracking`)
#                    lọc đơn GIAO THÀNH CÔNG + có order_source + hoá đơn chưa huỷ + loại
#                    chi nhánh nội bộ. Tất cả đều là TRẠNG THÁI VẬN HÀNH BIẾN ĐỔI (đơn
#                    có thể bị huỷ hoá đơn sau khi giao) chứ không phải danh tính đơn —
#                    cùng lập luận với "shipments" ở trên → tập CHA. Ba endpoint ghi của
#                    trang này (contact/appointment/order note) vẫn không lọt vì
#                    `CustomerResaleService._get_resale_tracking_order` đã áp lại đúng bộ
#                    lọc nghiệp vụ đó và trả 404 nếu đơn không thuộc trang; việc của
#                    record-scope ở đây là chốt AI được chạm đơn (staff/kênh), không phải
#                    lặp lại điều kiện hiển thị.
#
# BẮT BUỘC khai tường minh mọi section được truyền cho `order_record_scope_q`: lookup là
# fail-closed (xem `_section_identity_q`), section chưa khai sẽ raise ngay thay vì im
# lặng mở toàn bộ đơn công ty.
_SECTION_ORDER_IDENTITY_Q: dict[str, Q] = {
    "orders": Q(),
    "shipments": Q(),
    "customers": Q(),
    "warranty": Q(),
    "resale-tracking": Q(),
    "koc-orders": Q(order_source="koc"),
    "landing-orders": (
        Q(order_source__isnull=False)
        & ~Q(order_source="")
        & ~Q(order_source__in=["koc", *MARKETPLACE_ORDER_SOURCES])
    ),
}


def _section_identity_q(section: str) -> Q:
    """Điều kiện nhận dạng đơn của `section` — FAIL-CLOSED nếu chưa khai.

    Trước đây là `_SECTION_ORDER_IDENTITY_Q.get(section, Q())`: section chưa khai rơi
    về `Q()` rỗng, và nếu scope của user là ALL thì `section_q` cũng rỗng →
    `order_record_scope_q` return `Q()` → MỞ TOÀN BỘ đơn công ty. Một typo tên section
    (hoặc một section mới như "warranty"/"returns") đủ để vô hiệu hoá record-scope mà
    không có tín hiệu nào.

    Nay raise: sai cấu hình là lỗi lập trình, phải nổ ngay ở test/dev thay vì im lặng
    rò dữ liệu ở production. Section mới BẮT BUỘC khai vào `_SECTION_ORDER_IDENTITY_Q`
    kèm quyết định "tập cha hay có điều kiện danh tính".
    """
    try:
        return _SECTION_ORDER_IDENTITY_Q[section]
    except KeyError:
        raise ImproperlyConfigured(
            f"Section '{section}' chưa được khai trong _SECTION_ORDER_IDENTITY_Q "
            "(apps/orders/transport/views/order_views_impl/_view_helpers.py). "
            "Khai tường minh điều kiện nhận dạng đơn của section trước khi dùng "
            "order_record_scope_q — mặc định fail-open sẽ mở toàn bộ đơn công ty."
        ) from None


def order_record_scope_q(user, sections: Iterable[str]) -> Q:
    """Q khớp những đơn user được phép tác động, hợp trên NHIỀU section.

    Với mỗi section user có `{section}:view`: lấy (điều kiện nhận dạng đơn của section
    đó) AND (scope bản ghi của user ở section đó), rồi OR tất cả lại. Không section nào
    → `Q(pk__in=[])` (rỗng).

    Vì sao Q-union theo từng section thay vì chốt cứng một section:
    - Chốt cứng "orders" (cách cũ của `ORDER_WRITE_SCOPE_SECTION`) chặn OAN: role chỉ
      được cấp koc-orders/landing-orders không có scope ở section
      "orders" nên rơi về mặc định OWN, chỉ tác động được đơn của chính mình dù scope
      thật của section họ là all.
    - Lấy "section có scope rộng nhất" thì mở lỗ LEO QUYỀN NGANG: user KOC scope all sẽ
      tác động được cả đơn thường, vì scope all của koc-orders bị áp lên tập cha.
    Q-union đúng cả hai vế: user KOC scope all có Q = `order_source="koc"` nên KHÔNG
    chạm được đơn thường, còn user kho (shipments, tập cha) vẫn tác động được mọi đơn.

    `sections` phải là ĐÚNG danh sách truyền cho `require_section` của endpoint.
    """
    from apps.users.rbac import resolve_effective_permissions
    from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

    # Validate TOÀN BỘ section trước khi tính bất cứ thứ gì (fail-closed, xem
    # `_section_identity_q`). Không gộp vào vòng lặp bên dưới: ở đó một section gõ sai
    # sẽ bị nuốt bởi `continue` (không có `{section}:view`), bởi `return Q()` sớm của
    # section tập cha, hoặc bởi short-circuit superuser — lỗi cấu hình sẽ không bao giờ
    # lộ ra ở dev/test mà chỉ âm thầm nới scope ở production.
    # dict.fromkeys: bỏ section trùng nhưng giữ thứ tự → Q sinh ra ổn định, dễ đọc log.
    identity_by_section = {
        section: _section_identity_q(section) for section in dict.fromkeys(sections)
    }

    eff = resolve_effective_permissions(user)
    if eff.is_superuser:
        return Q()

    combined: Q | None = None
    for section, identity_q in identity_by_section.items():
        if not eff.can(f"{section}:view"):
            continue
        codes = resolved_channel_codes(user, section)
        if codes is OWN_SCOPE_SENTINEL:
            scope_q = Q(staff_id=user.id)
        elif codes is None:
            scope_q = Q()
        elif codes:
            scope_q = Q(channel__code__in=sorted(codes))
        else:
            # Scope channel/department mà chưa được gán kênh nào → section này không
            # khớp đơn nào. BỎ QUA thay vì OR vào: OR với một vế "không gì" là vô nghĩa,
            # còn nếu vô tình dùng Q() sẽ mở toàn bộ đơn công ty.
            continue
        section_q = identity_q & scope_q
        if not section_q:
            # Section tập cha + scope không giới hạn → hợp của MỌI vế là "tất cả đơn".
            # Bắt buộc return sớm: Django rút gọn `Q(x=1) | Q()` thành `Q(x=1)` (bỏ mất
            # vế rỗng), nên gộp bình thường sẽ SIẾT OAN đúng ca user scope all.
            return Q()
        combined = section_q if combined is None else (combined | section_q)

    if combined is None:
        # Không section nào cấp được gì (vd chỉ có `{section}:edit` mà không có `:view`,
        # hoặc mọi section đều là channel-scope chưa gán kênh) → không khớp đơn nào.
        return Q(pk__in=[])
    return combined


def scoped_order_or_error(
    request,
    order_id: int,
    sections: Iterable[str],
    *,
    select_related: tuple[str, ...] = (),
    prefetch_related: tuple[str, ...] = (),
):
    """Lấy MỘT đơn theo record-scope. Trả `(order, error_response)`.

    Phân biệt 404/403 đúng khuôn `_get_printable_order`: truy vấn `exists()` phụ CHỈ
    chạy ở nhánh bị chặn — đơn không tồn tại (hoặc đã xoá mềm) → 404, đơn có thật nhưng
    ngoài scope → 403. Không leak thêm gì so với hành vi cũ của các endpoint đơn lẻ.
    """
    queryset = Order.objects.filter(id=order_id, deleted_at__isnull=True)
    if select_related:
        queryset = queryset.select_related(*select_related)
    if prefetch_related:
        queryset = queryset.prefetch_related(*prefetch_related)
    order = queryset.filter(order_record_scope_q(request.user, sections)).first()
    if order is not None:
        return order, None
    if Order.objects.filter(pk=order_id, deleted_at__isnull=True).exists():
        return None, Response(
            {"detail": ORDER_RECORD_FORBIDDEN_DETAIL}, status=status.HTTP_403_FORBIDDEN
        )
    return None, Response(
        {"detail": ORDER_NOT_FOUND_DETAIL}, status=status.HTTP_404_NOT_FOUND
    )


class OrderSerializerContext(TypedDict, total=False):
    """All context keys consumed by OrderSerializer / OrderCRMSerializer.

    Pass a plain dict — TypedDict is a documentation/static-typing aid, not enforced at runtime.
    See `apps.orders.transport.serializers.order_serializers_impl.order_core_serializers`.
    """

    can_edit_fn: Callable[[str], bool]
    current_user_id: int | None
    next_reminder_map: dict
    kiotviet_job_map: dict
    kiotviet_active_ids: set
    skip_journey_events: bool
    crm_map: dict
    field_policy: dict
    duplicate_map: dict
    phone_visibility: PhoneVisibility


def order_field_policy(user) -> dict:
    """field_policy hiệu dụng cho user (rỗng nếu superuser). Tính qua resolver có cache."""
    from apps.users.rbac import resolve_effective_permissions

    eff = resolve_effective_permissions(user)
    return {} if eff.is_superuser else dict(eff.field_policy)


def order_base_context(user, **extra) -> dict:
    """Context TỐI THIỂU cho mọi serializer đơn ngoài luồng danh sách (detail, action,
    duplicate, hồ sơ khách...).

    Gom `field_policy` + `phone_visibility` vào MỘT chỗ: `OrderSerializer` che SĐT
    fail-closed khi thiếu `phone_visibility`, nên mỗi dict context viết tay ở view là
    một chỗ có thể quên. Call site chỉ còn `order_base_context(request.user,
    can_edit_fn=...)`.
    """
    return {
        "field_policy": order_field_policy(user),
        "phone_visibility": phone_visibility_for(user),
        **extra,
    }


def order_editable_fields(user) -> frozenset:
    """Field user được SỬA trên đơn. Superuser → toàn bộ catalog.

    Trả frozenset rỗng nghĩa là không sửa được gì (fail-closed) — đúng ý đồ với role
    chưa được cấp, KHÔNG phải lỗi cấu hình cần fallback.
    """
    from apps.users.rbac import resolve_effective_permissions
    from apps.users.rbac_catalog import ALL_ORDER_FIELDS, ORDER_GRANT_SECTION

    eff = resolve_effective_permissions(user)
    if eff.is_superuser:
        return ALL_ORDER_FIELDS
    return eff.editable_fields.get(ORDER_GRANT_SECTION, frozenset())


def _normalize_phone(value: str) -> str:
    return normalize_phone(str(value or ""))


def _extract_ingest_api_token(request) -> str:
    token = (request.headers.get("X-Api-Token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _parse_required_pagination(request, max_limit: int = 100):
    # Thin wrapper kept for backward-compatible imports; logic lives in apps.common.
    return parse_required_pagination(request, max_limit=max_limit)


# Các giá trị `sort` hợp lệ của mọi danh sách đơn. "newest"/"oldest" theo ngày
# TẠO đơn (`created_at`); "closed_*" theo ngày CHỐT (`sales_closed_at`);
# "amount_*" theo "Tổng sau giảm giá" (`total_after_discount`).
ORDERS_SORT_VALUES = (
    "newest",
    "oldest",
    "closed_newest",
    "closed_oldest",
    "amount_desc",
    "amount_asc",
)

# Đơn chưa chốt (`sales_closed_at` NULL) luôn xuống CUỐI ở CẢ HAI chiều — người
# dùng chọn sắp xếp theo ngày chốt là đang tìm đơn ĐÃ chốt. Mặc định của Postgres
# là NULLS FIRST cho DESC, sẽ đẩy 62% đơn NULL lên đầu.
_ORDERS_SORT_CLAUSES = {
    "newest": ("-created_at", "-id"),
    "oldest": ("created_at", "id"),
    "closed_newest": (F("sales_closed_at").desc(nulls_last=True), F("id").desc()),
    "closed_oldest": (F("sales_closed_at").asc(nulls_last=True), F("id").asc()),
    # Khớp index `ord_total_after_disc_idx` (total_after_discount, id) — DESC là
    # quét ngược cùng index; giữ `id` cùng chiều để không phải sort thêm.
    "amount_desc": ("-total_after_discount", "-id"),
    "amount_asc": ("total_after_discount", "id"),
}


def _parse_sort(request, default: str = "newest") -> str:
    source = request.data if request.method == "POST" else request.query_params
    value = str(source.get("sort") or "").strip().lower()
    if value not in ORDERS_SORT_VALUES:
        return default
    return value


def apply_orders_sort(queryset, sort_value: str, *, default: str = "newest"):
    """Áp thứ tự sắp xếp của danh sách đơn — dùng CHUNG cho cả 4 endpoint list.

    Trước đây mỗi endpoint tự viết một khối if/else giống hệt nhau, nên thêm một
    tuỳ chọn sắp xếp là phải sửa 4 chỗ và quên một chỗ thì lọc im lặng không chạy
    (cùng loại lỗi với 4 dict filter của trang đơn).

    Hai index partial `ord_sales_closed_{desc,asc}_idx` được đánh khớp ĐÚNG hai
    mệnh đề "closed_*" (và `ord_total_after_disc_idx` khớp "amount_*") bên dưới; đổi thứ tự cột hay chiều NULL ở đây mà không sửa
    index là mất luôn index-driven sort trên bảng 480k dòng.
    """
    clause = _ORDERS_SORT_CLAUSES.get(sort_value) or _ORDERS_SORT_CLAUSES[default]
    return queryset.order_by(*clause)


def scoped_orders_queryset(
    user, sections: Iterable[str] = ORDER_WRITE_SCOPE_SECTIONS, *, select_related=()
):
    """Queryset đơn (chưa xóa) mà user THẬT SỰ được tác động — dùng cho endpoint GHI.

    Vì sao cần: các endpoint bulk nhận `ids` từ client rồi `filter(id__in=ids)` thẳng,
    chỉ gate bằng `require_section` + `OrderService.can_edit_channel`. Nhưng
    `UserService.can_edit_order` trả True VÔ ĐIỀU KIỆN cho scope OWN/ALL (docstring nói
    "chặn theo owner ở tầng view" nhưng tầng view không chặn), nên một sale scope OWN
    xoá/huỷ/đẩy được đơn của bất kỳ ai chỉ bằng cách đổi `ids` (audit 2026-07-26 N1).

    Ghi phải mirror đọc: đơn không nhìn thấy trên danh sách thì cũng không được tác động.
    Record-scope tính bằng `order_record_scope_q` (hợp Q theo từng section) — xem
    docstring hàm đó để biết vì sao KHÔNG chốt cứng một section.

    Caller KHÔNG được bỏ gate `can_edit_channel` đang có — hàm này chỉ SIẾT thêm record
    scope; scope `department` mở kênh qua phòng ban trong khi `can_edit_channel` dựa
    trên UserChannel, nên hai lớp không thay thế được cho nhau.

    `sections` phải khớp danh sách section endpoint đang guard bằng `require_section`
    (vd transfer_landing_orders guard "landing-orders" → chỉ truyền section đó).
    """
    queryset = Order.objects.filter(deleted_at__isnull=True)
    if select_related:
        queryset = queryset.select_related(*select_related)
    return queryset.filter(order_record_scope_q(user, sections))


def build_orders_filters(request) -> dict:
    """Standard Orders-page filter dict (WITHOUT pagination).

    Shared by `list_orders` and the filter-based Excel export so both resolve the
    exact same set of orders — keeping the exported file in sync with what the list
    would show across all pages. Pagination (limit/offset/sort) is added by callers.
    """
    source = request.data
    return {
        "q": source.get("q", ""),
        "status": _parse_multi_query_values(request, "status"),
        "channel": _parse_multi_query_values(request, "channel"),
        # Loại kênh bán (Shopee/TikTok/Facebook/Web) — lọc theo CODE, token "none"
        # = kênh chưa phân loại. PHẢI khai ở CẢ BỐN dict filter của trang đơn
        # (build_orders_filters / list_koc_orders / list_landing_orders /
        # sheet3_view); thiếu một chỗ là bộ lọc im lặng không chạy đúng ở đó.
        "channel_type": _parse_multi_query_values(request, "channel_type"),
        "product": _parse_multi_query_values(request, "product"),
        "staff": _parse_multi_query_values(request, "staff"),
        "delivery_business_status": _parse_multi_query_values(request, "delivery_business_status"),
        "sales_status": _parse_multi_query_values(request, "sales_status"),
        "kiotviet_channel": _parse_multi_query_values(request, "kiotviet_channel"),
        "branch_id": _parse_multi_query_values(request, "branch_id"),
        # Kho xuất (FK warehouses.Warehouse) — nhiều id, token "none" = chưa gán kho.
        # Cũng phải khai ở CẢ BỐN dict filter như channel_type ở trên.
        "fulfillment_warehouse": _parse_multi_query_values(request, "fulfillment_warehouse"),
        "invoice_status": _parse_multi_query_values(request, "invoice_status"),
        "vat": _parse_multi_query_values(request, "vat"),
        "order_type": _parse_multi_query_values(request, "order_type"),
        # Nhãn KOC (sample_gift/renewal/recall) — rỗng = không lọc, không phải token
        # đặc biệt như "none" của shipping_type (xem koc_type_filter trong
        # repositories.py).
        "koc_order_type": _parse_multi_query_values(request, "koc_order_type"),
        # "none" là token riêng cho shipping_type IS NULL — chuỗi rỗng đã mang
        # nghĩa "không lọc" ở mọi trục khác nên không tái dùng được (xem
        # _apply_list_filters trong repositories.py).
        "shipping_type": _parse_multi_query_values(request, "shipping_type"),
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
        # Trang Đơn hàng chỉ hiển thị đơn show_orders=True (xem list_orders). Mặc định
        # "true" để export theo bộ lọc khớp đúng tập danh sách khi client bỏ trống.
        "show_orders": source.get("show_orders", "true"),
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        "return_date_from": source.get("return_date_from", ""),
        "return_date_to": source.get("return_date_to", ""),
        # "Ngày tạo trên KiotViet" (kiotviet_purchase_date). PHẢI khai ở CẢ NĂM dict
        # filter (build_orders_filters / list_koc_orders / list_landing_orders /
        # list_shipment_orders / sheet3_view) — xem _apply_list_filters.
        "kiotviet_purchase_date_from": source.get("kiotviet_purchase_date_from", ""),
        "kiotviet_purchase_date_to": source.get("kiotviet_purchase_date_to", ""),
    }


def _empty_orders_response(extra: dict | None = None):
    payload = {"results": [], "count": 0}
    if extra:
        payload.update(extra)
    return Response(payload)


def _build_order_list_context(user, page_orders, *, full_crm: bool = False) -> OrderSerializerContext:
    """Pre-compute per-request channel scope + batch next-reminder maps to avoid N+1.

    `full_crm=True` (sheet3): crm_map serialize bằng OrderCRMSerializer đầy đủ vì
    panel đánh giá sheet3 dùng chung BlockCrmRating với trang chi tiết. Các list
    khác dùng bản gọn OrderCRMListSerializer (audit FE 2026-08-04)."""
    from apps.orders.application.services.order_duplicate_warning_service import (
        build_duplicate_map,
    )
    from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
    from apps.orders.application.services.sale_reminder_service import get_next_reminder_at_map
    from apps.users.services import UserService

    allowed_codes = OrderServiceHelpers.assigned_channel_codes(user)

    def can_edit(channel_code: str) -> bool:
        return UserService.can_edit_order(user, channel_code, allowed_codes).can_edit

    next_reminder_map = get_next_reminder_at_map(o.id for o in page_orders)
    # Batch KiotViet job state for the page (2 queries) instead of 4 per-row subqueries.
    from apps.orders.repositories import OrderRepository

    kiotviet_job_map, kiotviet_active_ids = OrderRepository.kiotviet_job_maps(
        o.id for o in page_orders
    )
    crm_map = _build_crm_map(page_orders, next_reminder_map, full_crm=full_crm)
    # Cùng họ với `list-timing` ở order_list_view: query trùng quét theo SĐT nên
    # chi phí phụ thuộc lịch sử đơn của khách, không phụ thuộc số dòng — chỉ log
    # khi chậm để burst sau tự chỉ chỗ đau thay vì phải đoán.
    _dup_t0 = time.perf_counter()
    duplicate_map = build_duplicate_map(page_orders)
    _dup_ms = (time.perf_counter() - _dup_t0) * 1000
    if _dup_ms > 50:
        logger.info("dup-warning-timing rows=%d ms=%.0f", len(page_orders), _dup_ms)
    return {
        "can_edit_fn": can_edit,
        "current_user_id": getattr(user, "id", None),
        "next_reminder_map": next_reminder_map,
        "kiotviet_job_map": kiotviet_job_map,
        "kiotviet_active_ids": kiotviet_active_ids,
        # List table renders summary CRM only, never the timeline.
        "skip_journey_events": True,
        "crm_map": crm_map,
        "field_policy": order_field_policy(user),
        # Che SĐT khách của đơn sale khác (thiếu quyền `customer-phone:view`).
        "phone_visibility": phone_visibility_for(user),
        # Cảnh báo trùng SĐT ở cột SĐT của bảng — 1 query cho cả trang.
        "duplicate_map": duplicate_map,
    }


def _build_crm_map(page_orders, next_reminder_map: dict, *, full_crm: bool = False) -> dict:
    """Serialize the whole page's CRM assessments in ONE many=True pass, keyed by
    order id. OrderSerializer.get_crm then just looks the row up, so DRF binds
    the CRM serializer's fields once for the page instead of re-instantiating and
    deep-copying them for every row (the dominant serialize cost on full 100-row
    landing pages). List tables never render the journey timeline, so events are
    empty here (matching skip_journey_events). Mặc định dùng bản gọn
    OrderCRMListSerializer; sheet3 truyền full_crm=True (panel dùng chung với detail)."""
    from apps.orders.serializers import OrderCRMListSerializer, OrderCRMSerializer

    assessments = []
    order_ids = []
    for order in page_orders:
        assessment = getattr(order, "assessment", None)
        if assessment is not None:
            assessments.append(assessment)
            order_ids.append(order.id)
    if not assessments:
        return {}
    crm_ctx = {"journey_events": [], "next_reminder_map": next_reminder_map}
    serializer_cls = OrderCRMSerializer if full_crm else OrderCRMListSerializer
    rows = serializer_cls(assessments, many=True, context=crm_ctx).data
    return dict(zip(order_ids, rows))


def _parse_multi_query_values(request, key: str) -> list[str]:
    source = request.data if request.method == "POST" else request.query_params
    raw_values = (
        source.getlist(key)
        if hasattr(source, "getlist")
        else (source.get(key) if isinstance(source.get(key), list) else [])
    )
    if not raw_values:
        single = source.get(key, "")
        if isinstance(single, list):
            raw_values = single
        else:
            raw_values = [single] if single else []
    parsed: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for chunk in str(raw or "").split(","):
            value = chunk.strip()
            if not value or value in seen:
                continue
            parsed.append(value)
            seen.add(value)
    return parsed
