from __future__ import annotations

from django.db.models import Q
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.contracts.kiotviet_cancellation_port import (
    CancellationVerificationUnavailable,
    CancelVerdict,
    kiotviet_cancellation_port,
)
from apps.catalog.combo_lines import expand_order_lines
from apps.orders.crm_catalog import DISPATCHED_DELIVERY_CODE
from apps.orders.models import Order, OrderStatus
from apps.orders.order_status_constants import CANCELLED_INVOICE_STATUS
from apps.users.permissions import require_section
from apps.users.phone_visibility import (
    PhoneVisibility,
    is_public_phone_source,
    phone_visibility_for,
)


# Mode KHÔNG hỏi KiotViet: hàng hoàn thì đơn hủy vẫn phải nhập về kho được, và
# màn phiếu kho thủ công không được chết theo lỗi KiotViet (503).
_NO_VERIFY_MODES = frozenset({"return", "return_scan"})

# Mode mà `items` được dùng làm DÒNG HÀNG NHẬP LẠI KHO (quét trả hàng loạt →
# ReturnEntry; phiếu kho thủ công → StockDocument), chứ không chỉ để hiển thị.
# Chỉ những mode này mới nở SKU combo — xem `_expanded_items`.
_RETURN_LINE_MODES = frozenset({"return", "return_scan"})


# Serializer mô tả 1 đơn trả về bởi _serialize (dùng chung cho cả 2 nhánh response).
_LookupOrderSerializer = inline_serializer(
    "ShippingLookupOrder",
    fields={
        "order_id": drf_serializers.IntegerField(),
        "order_code": drf_serializers.CharField(),
        "shipping_code": drf_serializers.CharField(allow_null=True),
        "status": drf_serializers.IntegerField(),
        "status_label": drf_serializers.CharField(),
        "invoice_status": drf_serializers.IntegerField(allow_null=True),
        "customer_name": drf_serializers.CharField(allow_null=True),
        "customer_phone": drf_serializers.CharField(allow_null=True),
        "fulfillment_warehouse_id": drf_serializers.IntegerField(allow_null=True),
        "already_dispatched": drf_serializers.BooleanField(),
        "matched_by": drf_serializers.ChoiceField(choices=["shipping", "recall", "invoice"]),
        "items": inline_serializer(
            "ShippingLookupOrderItem",
            many=True,
            fields={
                "product_id": drf_serializers.IntegerField(allow_null=True),
                "sku": drf_serializers.CharField(allow_null=True),
                "name": drf_serializers.CharField(allow_null=True),
                "quantity": drf_serializers.IntegerField(),
            },
        ),
    },
)


def _expanded_items(o: Order, expand_combo: bool) -> list[dict]:
    """Dòng hàng của đơn; nở SKU combo ra MÃ LẺ cho các mode nhập hoàn.

    Bán 1 combo TRỪ TỒN theo mã lẻ, nên hạn mức phiếu hoàn cũng tính theo mã lẻ —
    trả dòng combo về màn quét thì thủ kho chọn ĐÚNG thứ UI gợi ý vẫn bị chặn "đơn
    nguồn chỉ còn 0 đơn vị chưa được cộng lại tồn" (lỗi prod 07/09, combo
    `DC30+NC01`). Luật nở nằm ở `catalog.combo_lines`, DÙNG CHUNG với panel khớp
    tay (`inventory.flow_pending_views`) — hai bản sao song song chính là nguyên
    nhân bản vá 20/08 chỉ bịt được một cửa.

    KHÔNG nở cho `mode=dispatch`: ở đó `items` chỉ để hiển thị / gộp "tổng hàng cần
    lấy", lines xuất kho do `issue_for_order` tự dựng từ `OrderItem` rồi nở bên
    trong — nở thêm ở đây chỉ đổi thứ nhân viên nhặt hàng nhìn thấy.
    """
    rows = [
        {
            "product_id": it.product_id,
            "sku": it.sku,
            "name": it.product_name,
            "quantity": it.quantity,
        }
        for it in o.items.all()
    ]
    return expand_order_lines(rows) if expand_combo else rows


def _serialize(
    o: Order,
    already_dispatched: bool = False,
    matched_by: str = "shipping",
    expand_combo: bool = False,
    visibility: PhoneVisibility | None = None,
) -> dict:
    # Màn quét kho: SĐT chỉ để đối chiếu/gọi khách khi hàng có vấn đề. Che với người
    # không có `customer-phone:view` trên đơn không phải của mình — cùng luật với
    # trang đơn, để không còn đường vòng lấy số bằng cách tra mã vận đơn.
    # FAIL-CLOSED khi thiếu (xem apps/users/phone_visibility.py).
    visibility = visibility or PhoneVisibility(user_id=None, can_view_full=False)
    return {
        "order_id": o.id,
        "order_code": o.order_code,
        "shipping_code": o.shipping_code,
        "status": int(o.status),
        "status_label": OrderStatus(o.status).label,
        "invoice_status": o.invoice_status,
        "customer_name": o.name,
        "customer_phone": visibility.apply(
            o.phone, o.staff_id, public_source=is_public_phone_source(o.order_source)
        ),
        "fulfillment_warehouse_id": o.fulfillment_warehouse_id,
        "already_dispatched": already_dispatched,
        "matched_by": matched_by,
        "items": _expanded_items(o, expand_combo),
    }


# Trả về 1 đơn khi khớp duy nhất, hoặc {"multiple": true, "orders": [...]} khi
# nhiều đơn cùng mã. Khai báo theo nhánh đơn (phổ biến nhất); nhánh multiple
# dùng chung cùng serializer item.
@extend_schema(
    parameters=[
        OpenApiParameter(name="code", type=str, required=True, description="Mã vận đơn hoặc mã thu hồi"),
        OpenApiParameter(
            name="mode",
            type=str,
            required=False,
            enum=["dispatch", "return", "return_scan"],
            description=(
                "dispatch (mặc định): xác minh realtime với KiotViet, chặn đơn đã hủy. "
                "return: không xác minh (đơn hủy vẫn được nhập hoàn) — dùng cho màn "
                "kéo dòng hàng của đơn vào phiếu kho thủ công. "
                "return_scan: như return, THÊM chặn mã đã có lần nhập hoàn còn sống "
                "— dùng cho quét nhập hoàn."
            ),
        ),
        OpenApiParameter(
            name="invoice_match",
            type=str,
            required=False,
            description=(
                "'1' để bật khớp thêm theo MÃ HOÁ ĐƠN (mode=dispatch only). Cờ triển "
                "khai: chỉ frontend bản MỚI (biết đọc `matched_by` và tự chọn kho theo "
                "đường khớp) mới gửi cờ này — một tab quét cũ để mở qua lúc deploy sẽ "
                "không gửi và tiếp tục nhận hình dạng response CŨ, tránh nhận nhầm đơn "
                "khớp invoice rồi trừ tồn ở kho màn quét qua dispatch-batch. Gỡ cờ này "
                "khi không còn bundle cũ nào lưu hành."
            ),
        ),
    ],
    responses={
        200: _LookupOrderSerializer,
        400: OpenApiResponse(description="Thiếu code"),
        404: OpenApiResponse(
            description=(
                "Không tìm thấy đơn theo mã vận đơn. Nếu mã đang chờ khớp "
                "(bảng PendingShipmentScan), body kèm thêm "
                '`pending_scan: {kind, created_at}`.'
            )
        ),
        409: OpenApiResponse(
            description=(
                "`order_cancelled`: đơn đã hủy trên KiotViet — không cho xuất kho. "
                "`return_already_scanned` (mode=return_scan): mã đã có lần nhập hoàn "
                "còn sống, body kèm `note_code` (mã phiếu, rỗng nếu mã còn chờ khớp)."
            )
        ),
        503: OpenApiResponse(
            description="Không xác minh được với KiotViet — chặn quét (fail-closed)."
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("shipments")])
def lookup_by_shipping_code(request):
    code = (request.query_params.get("code") or "").strip()
    if not code:
        return Response({"detail": "code is required."}, status=status.HTTP_400_BAD_REQUEST)
    # Query riêng theo từng field thay vì OR-join (Q(a) | Q(b__c)): Postgres không
    # thể tận dụng index qua join khi OR-expand điều kiện trên bảng liên kết, dẫn
    # đến seq-scan orders trên hot path quét kho. Gộp kết quả ở Python, dedup theo id.
    # select_related("kiotviet_channel"): verifier đọc order.kiotviet_channel cho TỪNG đơn →
    # thiếu nó là +1 SELECT lazy mỗi đơn ngay trên hot path quét kho. "assessment": cờ
    # already_dispatched đọc trạng thái giao hàng của TỪNG đơn (xem `_is_dispatched`).
    # order_by("-id"): đơn MỚI NHẤT đứng đầu — cùng luật với cron khớp mã pending
    # (`_process_one` lấy `order_by("-id")[:2]` rồi xuất `orders[0]`). Hai đường xử lý
    # cùng 1 mã phải chọn CÙNG một đơn, nếu không sẽ cho kết quả trái ngược nhau.
    ship_qs = (
        Order.objects.filter(shipping_code=code, deleted_at__isnull=True)
        .select_related("kiotviet_channel", "assessment")
        .prefetch_related("items")
        .order_by("-id")[:20]
    )
    recall_qs = (
        # Mỗi nhóm một mã nên equality là đủ (đảo ngược `__contains` của MR !1228,
        # vốn chỉ cần khi cột còn là mảng). distinct(): một đơn có 2 nhóm trùng mã
        # sẽ ra 2 dòng sau join.
        Order.objects.filter(recall_entries__recall_code=code, deleted_at__isnull=True)
        .select_related("kiotviet_channel", "assessment")
        .prefetch_related("items")
        .distinct()
        .order_by("-id")[:20]
    )
    # Quét XUẤT khớp thêm theo MÃ HOÁ ĐƠN (Task 7): đơn bán trực tiếp không có mã
    # vận đơn nên mã hoá đơn là thứ duy nhất kho quét được. CHỈ áp cho mode dispatch
    # — mode=return/return_scan ghi thẳng mã quét vào ReturnEntry.shipping_code, cho
    # mã hoá đơn lọt vào đấy là phá đối chiếu phiếu hoàn (cùng lý do §5.3 với cron).
    # Equality thuần, không __startswith: hoá đơn KiotViet xuất lại là "HD001.01",
    # quét "HD001" không được vô tình khớp bản xuất lại đó.
    mode = (request.query_params.get("mode") or "dispatch").strip().lower()
    # `invoice_match=1`: KHÔNG phải tham số thừa — nó là cửa khoá triển khai.
    # Ngay khi backend roll, khớp theo mã hoá đơn sẵn sàng chạy, nhưng đây là SPA
    # không có cơ chế force-reload theo build version: một tab quét mở từ TRƯỚC
    # lúc deploy (bundle cũ, không biết field `matched_by`) vẫn tiếp tục gọi
    # endpoint này suốt ca làm việc. Nếu nhánh invoice chạy vô điều kiện, tab đó
    # có thể nhận một đơn khớp theo mã hoá đơn, không phân biệt được với khớp mã
    # vận đơn, và gửi thẳng nó vào `dispatch-batch` với kho MÀN QUÉT — endpoint
    # đó còn ghi đè `order.fulfillment_warehouse`, nên hậu quả là trừ sai kho
    # CỘNG hỏng metadata, chỉ sửa được bằng un-dispatch rồi dispatch lại thủ công.
    # Chỉ frontend bản MỚI (đọc được `matched_by`, tự tách đường kho cho nhánh
    # invoice — xem scan-dispatch-groups.ts) mới gửi cờ này từ
    # `lookupByShippingCode` (scan-queries.ts). Một tab cũ không biết cờ này nên
    # không bao giờ gửi nó, và vì vậy không bao giờ nhận một match shape nó
    # không hiểu. Được phép GỠ cờ này khi không còn bundle cũ nào lưu hành nữa.
    invoice_match = (request.query_params.get("invoice_match") or "").strip() == "1"
    if mode not in _NO_VERIFY_MODES and invoice_match:
        invoice_qs = (
            Order.objects.filter(invoice_code=code, deleted_at__isnull=True)
            .select_related("kiotviet_channel", "assessment")
            .prefetch_related("items")
            .order_by("-id")[:20]
        )
    else:
        invoice_qs = Order.objects.none()
    orders = list(ship_qs)
    matched_by: dict[int, str] = {o.id: "shipping" for o in orders}
    for o in recall_qs:
        if o.id not in matched_by:
            orders.append(o)
            matched_by[o.id] = "recall"
    for o in invoice_qs:
        if o.id not in matched_by:
            orders.append(o)
            matched_by[o.id] = "invoice"
    orders = orders[:20]
    if not orders:
        body: dict = {"detail": "not_found"}
        # Mã đã nằm bảng chờ → FE báo "đã ghi nhận, đang chờ khớp" thay vì thêm dòng mới.
        from apps.inventory.models import (
            PendingScanKind,
            PendingScanStatus,
            PendingShipmentScan,
        )

        pending = (
            PendingShipmentScan.objects.filter(code=code, voided_at__isnull=True)
            .filter(
                # Quét XUẤT giữ luật cũ (chỉ mã CÒN chờ khớp mới chặn) — mã đã khớp
                # rồi vẫn phải quét xuất lại được (hàng gửi bù dùng lại mã).
                # Quét HOÀN thì MỌI trạng thái đều tính: 1 mã = 1 lần bắn, đã khớp
                # xong mà vẫn cho bắn lại chính là con bug này.
                Q(status=PendingScanStatus.PENDING)
                | Q(kind=PendingScanKind.RETURN)
            )
            .order_by("-created_at")
            .first()
        )
        if pending is not None:
            body["pending_scan"] = {
                "kind": pending.kind,
                "created_at": pending.created_at.isoformat(),
            }
        return Response(body, status=status.HTTP_404_NOT_FOUND)
    # Quét XUẤT giao: DB có thể trễ tới 30' so với KiotViet → hỏi thẳng nguồn sự thật.
    # Quét NHẬP hoàn không xác minh: đơn hủy thì hàng về kho là đúng nghiệp vụ.
    if mode == "return_scan":
        # Quét NHẬP HOÀN: 1 mã = 1 lần bắn. Chặn ngay tại cửa đầu tiên người dùng
        # chạm vào, thay vì để lọt tới lúc bấm Xác nhận rồi mới báo lỗi từng dòng.
        # KHÔNG áp cho mode="return" thuần: màn phiếu kho thủ công dùng mode đó chỉ
        # để KÉO DÒNG HÀNG của đơn (không tạo lần nhập hoàn nào), chặn ở đó là hỏng
        # một chức năng không liên quan.
        blocked = _return_already_scanned(code)
        if blocked is not None:
            return blocked
    if mode not in _NO_VERIFY_MODES:
        # CHỈ xác minh đơn mới nhất (orders[0]) — đúng đơn mà cron/`_complete_dispatch` sẽ xuất.
        # Lặp TẤT CẢ đơn cùng mã sẽ chặn OAN cả ca hợp lệ: 1 mã vận đơn dùng lại cho đơn cũ (đã
        # hủy) + đơn mới (còn sống) → kho không xuất được đơn mới và không có đường thoát.
        # Đơn hủy nằm trong danh sách "multiple" vẫn bị chặn ở chốt cuối `dispatch_orders_batch`
        # (check per-order, đọc DB tươi + hỏi KiotViet) nên tồn kho không bao giờ bị trừ nhầm.
        blocked = _verify_not_cancelled(orders[0])
        if blocked is not None:
            return blocked

    # Sau gate verify: request bị chặn 409/503 thì không tốn query này.
    dispatched_ids = _dispatched_order_ids([o.id for o in orders])

    expand_combo = mode in _RETURN_LINE_MODES
    visibility = phone_visibility_for(request.user)

    if len(orders) > 1:
        return Response(
            {
                "multiple": True,
                "orders": [
                    _serialize(
                        o,
                        _is_dispatched(o, dispatched_ids),
                        matched_by[o.id],
                        expand_combo,
                        visibility,
                    )
                    for o in orders
                ],
            }
        )
    return Response(
        _serialize(
            orders[0],
            _is_dispatched(orders[0], dispatched_ids),
            matched_by[orders[0].id],
            expand_combo,
            visibility,
        )
    )


def _return_already_scanned(code: str) -> Response | None:
    """Trả 409 nếu mã đã có lần nhập hoàn còn sống; None nếu mã còn tự do.

    Luật "còn sống" nằm ở một chỗ duy nhất (`inventory.pending_scan_service`) để ba
    cửa chặn — tra mã, ghi mã chờ khớp, tạo nhóm đơn — không lệch nhau. Import cục
    bộ như nhánh pending bên trên: giữ orders không phụ thuộc inventory ở module level.
    """
    from apps.inventory.application.services.pending_scan_service import live_return_scan

    live = live_return_scan(code)
    if live is None:
        return None
    return Response(
        {
            "detail": "return_already_scanned",
            # Rỗng = mã mới bắn, chưa ra phiếu (đang chờ khớp đơn).
            "note_code": live["note_code"],
            "scanned_at": live["scanned_at"],
        },
        status=status.HTTP_409_CONFLICT,
    )


def _verify_not_cancelled(order: Order) -> Response | None:
    """Trả Response chặn (409/503) nếu ĐƠN NÀY bị hủy / không xác minh được."""
    try:
        check = kiotviet_cancellation_port().verify_not_cancelled(order)
    except CancellationVerificationUnavailable:
        return Response(
            {"detail": "verify_unavailable"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if check is CancelVerdict.CANCELLED:
        return Response(
            {"detail": "order_cancelled", "order_code": order.order_code},
            status=status.HTTP_409_CONFLICT,
        )
    # UNVERIFIABLE (đơn ngoài KiotViet / kill-switch tắt / 404) → DB là nguồn sự thật.
    if check is CancelVerdict.UNVERIFIABLE and (
        int(order.status) == int(OrderStatus.CANCELLED)
        or order.invoice_status == CANCELLED_INVOICE_STATUS
    ):
        return Response(
            {"detail": "order_cancelled", "order_code": order.order_code},
            status=status.HTTP_409_CONFLICT,
        )
    return None


def _is_dispatched(order: Order, dispatched_ids: set[int]) -> bool:
    """Đơn đã xuất kho chưa? Sổ tồn HOẶC trạng thái giao hàng là đủ.

    Sổ tồn một mình là chưa đủ: đơn không khớp được sản phẩm nào (`create_order_manual`
    chỉ tạo OrderItem khi có `matched_items`) xuất kho với `lines=[]` → không sinh bút
    toán nào, cờ chỉ đọc sổ sẽ báo "chưa xuất" vĩnh viễn. Kho quét lại được mà không
    có cảnh báo, bấm Xác nhận cũng không ghi thêm gì (trạng thái giao hàng không đổi).
    Cùng lỗ hổng đó với đơn xuất lại bị bỏ rơi sổ (docs/reissue-ledger-leak-2026-07-21.md).
    """
    if order.id in dispatched_ids:
        return True
    assessment = getattr(order, "assessment", None)
    return getattr(assessment, "delivery_business_status_code", None) == DISPATCHED_DELIVERY_CODE


def _dispatched_order_ids(order_ids: list[int]) -> set[int]:
    """Đơn còn tồn NET đã trừ chưa hoàn (SALE - SALE_REVERSAL > 0) → coi là đã xuất kho.

    Không dùng exists(SALE) đơn thuần: đơn từng xuất rồi bị un-dispatch (hoàn hết,
    net = 0) không còn được coi là đã xuất, phải cho quét lại.
    """
    from apps.inventory.application.services.stock_sales_service import (
        order_ids_with_outstanding_stock,
    )

    return order_ids_with_outstanding_stock(order_ids)
