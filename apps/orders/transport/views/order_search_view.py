from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.repositories import OrderRepository
from apps.orders.transport.views.order_views_impl._view_helpers import order_record_scope_q
from apps.users.permissions import ORDER_SECTIONS, require_section
from apps.users.phone_visibility import is_public_phone_source, phone_visibility_for

_MAX_LIMIT = 20

# Phải khớp ĐÚNG danh sách truyền cho `require_section` bên dưới: quyền vào endpoint là
# "có BẤT KỲ section nào trong danh sách" nên record-scope cũng phải là HỢP của chúng.
# Bỏ "warranty" ra thì user CSKH bảo hành (chỉ có section đó) mất sạch kết quả tra cứu.
_SEARCH_SCOPE_SECTIONS = ("warranty", *ORDER_SECTIONS)

_SearchResultSerializer = inline_serializer(
    "OrderSearchResult",
    many=True,
    fields={
        "id": drf_serializers.IntegerField(),
        "name": drf_serializers.CharField(),
        "phone": drf_serializers.CharField(),
        "address": drf_serializers.CharField(),
        "product": drf_serializers.CharField(),
        "sku": drf_serializers.CharField(),
        "quantity": drf_serializers.IntegerField(),
        "total_after_discount": drf_serializers.CharField(),
        "shipping_code": drf_serializers.CharField(),
        "order_code": drf_serializers.CharField(),
    },
)


@extend_schema(
    parameters=[
        OpenApiParameter(name="q", type=str, required=True, description="SĐT / mã vận đơn / mã đơn / tên"),
        OpenApiParameter(name="limit", type=int, required=False),
    ],
    responses={200: _SearchResultSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*_SEARCH_SCOPE_SECTIONS)])
def search_orders(request):
    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return Response([])
    try:
        limit = min(int(request.query_params.get("limit", _MAX_LIMIT)), _MAX_LIMIT)
    except (TypeError, ValueError):
        limit = _MAX_LIMIT
    limit = max(1, limit)
    # Dùng CHUNG bộ search đã tối ưu index của danh sách đơn (union id theo trigram:
    # phone_normalized / shipping / invoice / old_invoice / order_code contains + name
    # icontains qua functional index) thay vì OR icontains cũ — vốn sinh
    # `UPPER(col::text) LIKE` không dùng được trigram → seq-scan toàn bảng (~2.6s).
    # include_cancelled=True: search phải tìm được cả đơn đã huỷ (q bỏ qua filter khung).
    base = OrderRepository._base_orders_queryset(include_cancelled=True)
    # Record-scope: trước fix endpoint KHÔNG lọc gì. Vì `q` khớp
    # phone/shipping_code/invoice_code/order_code/name và response trả `name` + `phone`
    # (KHÔNG mask) + `address` + `total_after_discount`, bất kỳ user nào có 1 section đơn
    # đều dùng được endpoint làm oracle liệt kê toàn bộ CSDL khách 20 dòng/lượt
    # (audit 2026-07-26, phía ĐỌC). Ghi phải mirror đọc: đơn không thấy trên danh sách
    # thì cũng không được tra ra ở đây.
    rows = (
        OrderRepository._apply_list_filters(base, {"q": q})
        .filter(order_record_scope_q(request.user, _SEARCH_SCOPE_SECTIONS))
        .order_by("-created_at")[:limit]
    )
    # Che SĐT của đơn KHÔNG do người tra cứu phụ trách (thiếu `customer-phone:view`).
    # Bộ lọc `q` phía trên KHÔNG đổi: tra theo số đầy đủ vẫn ra đúng đơn — che là ở
    # ĐẦU RA, để endpoint không thành máy liệt kê SĐT 20 dòng/lượt cho sale khác.
    visibility = phone_visibility_for(request.user)
    return Response(
        [
            {
                "id": o.id,
                "name": o.name,
                "phone": visibility.apply(
                    o.phone,
                    o.staff_id,
                    public_source=is_public_phone_source(o.order_source),
                ),
                "address": o.address,
                "product": o.product,
                "sku": o.sku,
                "quantity": o.quantity,
                "total_after_discount": str(o.total_after_discount),
                "shipping_code": o.shipping_code,
                "order_code": o.order_code,
            }
            for o in rows
        ]
    )
