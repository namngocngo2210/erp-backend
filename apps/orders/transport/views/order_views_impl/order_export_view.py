import logging

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from apps.common.throttling import ClientIpMixin, ThrottleGuardMixin
from apps.users.permissions import ORDER_SECTIONS, require_permission, require_section
from apps.users.phone_visibility import phone_visibility_for

from apps.orders.invoice import (
    bulk_delivery_slip_response,
    bulk_invoice_response,
    delivery_slip_response,
    invoice_response,
)
from apps.orders.models import Order

from ._view_helpers import _apply_user_channel_scope

logger = logging.getLogger(__name__)

# Trần số đơn cho một lần in gộp. Mỗi đơn là MỘT lượt gọi HTTP sang KiotViet
# (invoice.py: bulk_invoice_response lặp get_invoice_by_code), nên `ids` không
# chặn trần đồng nghĩa với việc client tự tạo được vài nghìn round-trip trong
# một request — vừa treo worker vừa đốt rate-limit của KiotViet.
PRINT_MAX_IDS = 200

# Trần giá trị id. Order.id là BigAutoField (int8) nên số vượt miền bigint sẽ khiến
# `filter(id__in=ids)` raise ở tầng driver — NGOÀI try/except của view → 500 + noise
# Sentry cho một lỗi vốn là của client. Chặn ngay ở tầng transport thành 400.
_MAX_ORDER_ID = 9_223_372_036_854_775_807

# Thông báo 403 khi user có quyền vào trang đơn nhưng không được xem bản ghi này.
_PRINT_FORBIDDEN_DETAIL = "Bạn không có quyền xem đơn hàng này."

# Message 400 cho body `ids`. Tách thành hằng vì test khoá đúng chuỗi này: 400 còn
# phát sinh từ invoice.py ("Không có hóa đơn nào.") nên chỉ assert status_code là
# tautology — xoá sạch check trần mà test vẫn xanh.
_NO_IDS_DETAIL = "Chưa chọn đơn hàng."
_INVALID_IDS_DETAIL = "Danh sách đơn không hợp lệ."
_PRINT_CAP_DETAIL = f"Chỉ in được tối đa {PRINT_MAX_IDS} đơn mỗi lần."


# Section dùng để resolve record-scope cho 4 endpoint in ấn. Khác bulk_export_excel,
# request in ấn KHÔNG mang `section` nên không thể resolve theo trang gọi.
#
# Cố tình chọn "orders" chứ không phải "section rộng nhất mà user được cấp": trang
# Đơn hàng là tập CHA (list_orders không khóa dòng), còn koc/landing khóa cứng theo
# order_source. Lấy scope của koc-orders (vd scope=all) mà áp lên endpoint in dùng
# chung sẽ cho user KOC in cả đơn thường — đúng loại rò rỉ mà task này đang bịt.
# Đánh đổi: user CHỈ được cấp koc/landing sẽ rơi vào scope mặc định OWN nên chỉ in
# được đơn của chính mình; muốn nới thì FE phải gửi `section`
# như bulk_export_excel (xem docs/audit-2026-07-26-full-stack.md, mục N1).
_PRINT_SCOPE_SECTION = "orders"

# Quyền GHI cho ĐƯỜNG IN PHIẾU GIAO (`/<id>/delivery-slip`, `/bulk-delivery-slips`).
#
# Vì sao in phiếu giao cần quyền ghi còn in hóa đơn thì không: `delivery_slip_response`
# / `bulk_delivery_slip_response` set `Order.is_delivery_invoice_printed = True`
# (invoice.py). Trước fix, 2 endpoint đó chỉ guard `{section}:view`, nên một role CHỈ
# ĐỌC gọi `POST /bulk-delivery-slips` với 200 id là lật cờ "đã in" của 200 đơn — kho
# lọc "chưa in" sẽ BỎ SÓT đúng 200 đơn đó (audit 2026-07-26 N3: mutate sau quyền đọc).
#
# Chọn "yêu cầu quyền ghi" thay vì "tách cờ ra một hành động riêng": cờ này CHÍNH LÀ
# biên bản của hành vi in, nghiệp vụ kho lọc theo nó. Tách ra endpoint riêng thì hoặc
# FE phải gọi 2 lượt (lượt 2 fail/bị bỏ qua = mất cờ, đơn bị in lại), hoặc user chỉ có
# `:view` in xong mà cờ không nhúc nhích — đổi một lỗ hổng quyền lấy một lỗ hổng dữ
# liệu. Giữ cờ gắn liền với đường in là ATOMIC và không cần đổi FE.
#
# Là HỢP các `{section}:edit` (không chốt cứng "orders:edit") để khớp khuôn
# `require_section`: endpoint dùng chung 4 trang đơn, vào được = có BẤT KỲ section nào,
# nên role chỉ vận hành trang KOC/Landing/Outside vẫn phải in được.
_DELIVERY_SLIP_WRITE_CODES = tuple(f"{section}:edit" for section in ORDER_SECTIONS)


class OrderPrintRateThrottle(ThrottleGuardMixin, ClientIpMixin, SimpleRateThrottle):
    """Trần tần suất dùng chung cho 4 endpoint in ấn (rate ở REST_FRAMEWORK).

    Vì sao cần: mỗi request in là tối đa `PRINT_MAX_IDS` (=200) round-trip HTTP TUẦN TỰ
    sang KiotViet. Trần 200 id chỉ chặn chiều RỘNG của MỘT request; không có trần tần
    suất thì một tab kẹt vòng lặp (hoặc script) đủ để giữ hết worker gunicorn và đốt
    rate-limit KiotViet của cả công ty (audit 2026-07-26 N4).

    Dùng SimpleRateThrottle (KHÔNG phải ScopedRateThrottle) vì view dạng hàm @api_view
    không có thuộc tính `throttle_scope` — ScopedRateThrottle sẽ đọc scope=None rồi bỏ
    qua throttle. Cùng lý do với `apps.users.views.LoginRateThrottle`.

    MỘT scope chung cho cả 4 endpoint: chi phí thật (round-trip KiotViet) là như nhau,
    nếu tách bucket theo endpoint thì chỉ cần xoay vòng 4 URL là nhân 4 lần tần suất.

    Key theo USER khi đã đăng nhập, chỉ rơi về IP client (ClientIpMixin, đúng IP thật
    sau Cloudflare+nginx) khi ẩn danh: kho thường in qua CHUNG một đường NAT, key theo
    IP sẽ gộp cả kho vào một bucket và chặn oan người thứ hai.
    """

    scope = "order_print"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = (
            f"user-{user.pk}"
            if getattr(user, "is_authenticated", False)
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}


class _PrintIdsSerializer(serializers.Serializer):
    """Validate body `{"ids": [...]}` của 2 endpoint in gộp.

    Vì sao là serializer chứ không phải isinstance/len tự viết: `{"ids": ["abc"]}`
    hay số vượt miền bigint đi thẳng vào `filter(id__in=ids)` sẽ raise
    ValueError/DataError NGOÀI try/except của view → 500 cho một lỗi vốn là của
    client. Đây đúng khuôn `OrderBulkDeleteSerializer` và
    `best_express.CodesRequestSerializer` (max_length chặn abuse upstream).

    error_messages tiếng Việt vì FE đọc trực tiếp khoá `detail` của response 400
    (frontend useOrdersTableData.printHtmlFromApi) và hiển thị nguyên văn cho user —
    chuỗi mặc định tiếng Anh của DRF sẽ lộ ra UI.
    """

    ids = serializers.ListField(
        child=serializers.IntegerField(
            min_value=1,
            max_value=_MAX_ORDER_ID,
            error_messages={
                "invalid": _INVALID_IDS_DETAIL,
                "min_value": _INVALID_IDS_DETAIL,
                "max_value": _INVALID_IDS_DETAIL,
            },
        ),
        allow_empty=False,
        max_length=PRINT_MAX_IDS,
        error_messages={
            "required": _NO_IDS_DETAIL,
            "null": _NO_IDS_DETAIL,
            "empty": _NO_IDS_DETAIL,
            "not_a_list": _INVALID_IDS_DETAIL,
            "max_length": _PRINT_CAP_DETAIL,
        },
    )


def _first_error_detail(errors) -> str:
    """Làm phẳng cây lỗi DRF thành MỘT câu cho khoá `detail`.

    ListField báo lỗi phần tử theo dict {index: [message]}, còn FE chỉ biết đọc
    `detail` dạng chuỗi — trả nguyên `serializer.errors` sẽ hiện ra thông báo rỗng.
    """
    if isinstance(errors, str):
        return str(errors)
    if isinstance(errors, dict):
        return next(
            (_first_error_detail(value) for value in errors.values()), _INVALID_IDS_DETAIL
        )
    if isinstance(errors, list):
        return next((_first_error_detail(value) for value in errors), _INVALID_IDS_DETAIL)
    return _INVALID_IDS_DETAIL


def _print_ids_from_request(request) -> tuple[list[int], Response | None]:
    """Validate `ids` ở tầng transport. Trả về (ids, error_response).

    Cố ý tách khỏi `_scoped_print_queryset` để hàm scope chỉ làm scope: khi validate
    còn nằm trong đó, 2 endpoint in đơn lẻ buộc phải bỏ im lặng slot error
    (`orders, _ = ...`) — hiện luôn None nên chưa sai, nhưng là bẫy cho lần sửa sau.
    """
    serializer = _PrintIdsSerializer(data=request.data)
    if serializer.is_valid():
        return serializer.validated_data["ids"], None
    return [], Response(
        {"detail": _first_error_detail(serializer.errors)},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _scoped_print_queryset(request):
    """Queryset đơn mà user THẬT SỰ được xem, dùng chung cho 4 endpoint in ấn.

    Cùng khuôn scope với bulk_export_excel: `own` → lọc theo staff_id, còn
    channel/department → lọc theo channel code được cấp (audit 2026-07-26 N1).

    Cố ý KHÔNG gate thêm bằng `OrderService.can_edit_channel`: in ấn là hành vi
    ĐỌC nên phải mirror đúng read-scope. can_edit_channel dựa trên UserChannel,
    trong khi scope `department` mở kênh qua phòng ban — gate bằng nó sẽ chặn oan
    user department-scope đang thấy đơn đó trên danh sách.
    """
    scope_filters, scope_empty = _apply_user_channel_scope(
        {}, request.user, _PRINT_SCOPE_SECTION
    )
    queryset = Order.objects.select_related("kiotviet_channel", "channel").filter(
        deleted_at__isnull=True
    )
    if scope_empty:
        # Nhánh BẮT BUỘC: user scope channel/department mà chưa được gán kênh nào có
        # scope_filters KHÔNG chứa cả owner_user_id lẫn channel, nên 2 nhánh filter
        # dưới đây đều không chạy → queryset không bị lọc gì → rò TOÀN BỘ đơn công ty.
        return queryset.none()
    if scope_filters.get("owner_user_id"):
        queryset = queryset.filter(staff_id=scope_filters["owner_user_id"])
    elif scope_filters.get("channel"):
        queryset = queryset.filter(channel__code__in=scope_filters["channel"])
    return queryset


def _get_printable_order(request, order_id: int):
    """Lấy đơn theo scope cho 2 endpoint in đơn lẻ.

    Trả về (order, error_response). Phân biệt 404/403 bằng một truy vấn `exists()`
    CHỈ chạy ở nhánh bị chặn: đơn không tồn tại → 404, đơn tồn tại nhưng ngoài
    scope → 403. Không leak thêm gì so với các endpoint đơn lẻ khác (close_case,
    update_order_inline) vì chúng cũng 404-rồi-403 theo đúng thứ tự này.
    """
    order = _scoped_print_queryset(request).filter(pk=order_id).first()
    if order is not None:
        return order, None
    if Order.objects.filter(pk=order_id, deleted_at__isnull=True).exists():
        return None, Response(
            {"detail": _PRINT_FORBIDDEN_DETAIL}, status=status.HTTP_403_FORBIDDEN
        )
    return None, Response(
        {"detail": "Không tìm thấy đơn hàng."}, status=status.HTTP_404_NOT_FOUND
    )


# Trả HTML hóa đơn để in (không phải JSON-contract) → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
@throttle_classes([OrderPrintRateThrottle])
@never_cache
def download_order_invoice(request, order_id: int):
    """Fetch invoice from KiotViet API and return as printable HTML."""
    order, error = _get_printable_order(request, order_id)
    if error is not None:
        return error

    if not order.invoice_code:
        return Response(
            {"detail": "Đơn hàng chưa có mã hóa đơn. Vui lòng đẩy đơn lên KiotViet trước."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not order.kiotviet_channel:
        return Response(
            {"detail": "Đơn hàng chưa được gán kênh KiotViet."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # Che SĐT khách của đơn không phải của mình như mọi màn REST
        # (docs/customer-phone-masking.md, audit 2026-09-23 M3).
        return invoice_response(order, visibility=phone_visibility_for(request.user))
    except Exception:
        logger.exception("Failed to generate invoice for order_id=%s", order_id)
        return Response(
            {"detail": "Không thể lấy hóa đơn từ KiotViet."},
            status=status.HTTP_502_BAD_GATEWAY,
        )


# Trả HTML phiếu giao hàng để in (không phải JSON-contract) → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes(
    [
        IsAuthenticated,
        require_section(*ORDER_SECTIONS),
        # In phiếu giao GHI cờ `is_delivery_invoice_printed` → xem _DELIVERY_SLIP_WRITE_CODES.
        require_permission(*_DELIVERY_SLIP_WRITE_CODES),
    ]
)
@throttle_classes([OrderPrintRateThrottle])
@never_cache
def download_order_delivery_slip(request, order_id: int):
    """Fetch invoice detail from KiotViet Web API and return delivery slip HTML."""
    order, error = _get_printable_order(request, order_id)
    if error is not None:
        return error

    if not order.invoice_id:
        return Response(
            {"detail": "Đơn hàng chưa có invoice_id. Vui lòng đẩy đơn lên KiotViet trước."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not order.kiotviet_channel:
        return Response(
            {"detail": "Đơn hàng chưa được gán kênh KiotViet."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        return delivery_slip_response(order)
    except Exception:
        logger.exception("Failed to generate delivery slip for order_id=%s", order_id)
        return Response(
            {"detail": "Không thể lấy phiếu giao hàng từ KiotViet."},
            status=status.HTTP_502_BAD_GATEWAY,
        )


# Trả HTML gộp nhiều hóa đơn để in (không phải JSON-contract) → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
@throttle_classes([OrderPrintRateThrottle])
@never_cache
def bulk_download_invoices(request):
    ids, error = _print_ids_from_request(request)
    if error is not None:
        return error

    orders = _scoped_print_queryset(request).filter(id__in=ids)
    try:
        return bulk_invoice_response(orders, visibility=phone_visibility_for(request.user))
    except Exception:
        logger.exception("Failed to generate bulk invoices")
        return Response(
            {"detail": "Không thể tạo hóa đơn."},
            status=status.HTTP_502_BAD_GATEWAY,
        )


# Trả HTML gộp nhiều phiếu giao hàng để in (không phải JSON-contract) → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["POST"])
@permission_classes(
    [
        IsAuthenticated,
        require_section(*ORDER_SECTIONS),
        # In phiếu giao GHI cờ `is_delivery_invoice_printed` → xem _DELIVERY_SLIP_WRITE_CODES.
        require_permission(*_DELIVERY_SLIP_WRITE_CODES),
    ]
)
@throttle_classes([OrderPrintRateThrottle])
@never_cache
def bulk_download_delivery_slips(request):
    ids, error = _print_ids_from_request(request)
    if error is not None:
        return error

    orders = _scoped_print_queryset(request).filter(id__in=ids)
    try:
        return bulk_delivery_slip_response(orders)
    except Exception:
        logger.exception("Failed to generate bulk delivery slips")
        return Response(
            {"detail": "Không thể tạo phiếu giao hàng."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

