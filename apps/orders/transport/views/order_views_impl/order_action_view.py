import logging

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.phone_visibility import is_public_phone_source, phone_visibility_for
from apps.users.permissions import (
    ORDER_CREATE_CODES,
    ORDER_DELETE_CODES,
    ORDER_EDIT_CODES,
    ORDER_SECTIONS,
    can_assign_sale,
    require_method_permission,
    require_permission,
)

from apps.customers.customer_type import refresh_customer_type
from apps.orders.application.services.order_kiotviet_job_service import OrderKiotVietJobService
from apps.orders.assessment_fields import AssessmentValidationError
from apps.orders.contracts import OrderEventContract
from apps.orders.field_diff import (
    CRM_FIELD_PREFIX,
    VALIDATED_KEY_BY_FIELD,
    changed_fields,
)
from apps.orders.kiotviet_validation import build_kiotviet_push_precheck
from apps.orders.models import Order, OrderKiotVietJob
from apps.orders.serializers import (
    OrderBulkDeleteSerializer,
    OrderInlineUpdateSerializer,
    OrderKiotVietActionSerializer,
    OrderSerializer,
)
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.invoice_lock import (
    describe_locked_fields,
    is_invoice_locked,
    locked_field_changes,
)
from apps.dispatch import dispatch
from apps.users.rbac_catalog import ALL_ORDER_FIELDS, ORDER_FIELD_LABELS

from ._view_helpers import (
    INLINE_SCOPE_SECTIONS,
    ORDER_WRITE_SCOPE_SECTIONS,
    order_editable_fields,
    order_base_context,
    scoped_order_or_error,
    scoped_orders_queryset,
)

logger = logging.getLogger(__name__)

# Hai action đi chung MỌI thứ: cùng guard (kênh KiotViet, kho xuất bắt buộc, chặn đơn
# đã có order_code), cùng job ACTION_CREATE_INVOICE, cùng ghi
# direct_dispatch_deferred=True. `quick_sale_selected` (đường trừ tồn ngay) đã bị gỡ khỏi
# UI từ 2026-08-28 nhưng enum được GIỮ LẠI để tab đang mở của sale không ăn 400 lúc
# deploy — nó nay chỉ là bí danh của `quick_sale_deferred_selected`. TUYỆT ĐỐI không thêm
# giá trị mới vào OrderKiotVietJob.ACTION_* cho quick-sale: có 4 chỗ trong codebase so
# job.action == "create_invoice", thêm enum mới làm cả 4 chỗ bỏ sót đơn xuất sau.
_QUICK_SALE_ACTIONS = {
    OrderKiotVietActionSerializer.ACTION_QUICK_SALE_SELECTED,
    OrderKiotVietActionSerializer.ACTION_QUICK_SALE_DEFERRED_SELECTED,
}

# transfer_landing_orders guard bằng require_permission("landing-orders:edit") nên
# record-scope chỉ được resolve theo ĐÚNG section đó, không dùng
# ORDER_WRITE_SCOPE_SECTIONS: quyền vào endpoint này chỉ đến từ landing-orders nên
# hợp thêm section khác sẽ nới oan.
_LANDING_SCOPE_SECTIONS = ("landing-orders",)

# Trần số đơn còn được đính kèm row vào event `order.updated` khi chuyển Landing →
# Đơn hàng. Xem khối giải thích trong `transfer_landing_orders`.
_TRANSFER_ROW_PAYLOAD_MAX = 50


def _skipped_orders_payload(user, orders) -> list[dict]:
    """Danh sách đơn bị BỎ QUA của các action hàng loạt KiotViet (thiếu mã hoá đơn).

    Kèm SĐT để người thao tác đối soát ngay trên dialog — che theo đúng luật màn
    hình (`customer-phone:view` / đơn của mình), nếu không thì một cú bấm "Huỷ hoá
    đơn" trên tập đơn chọn bừa sẽ trả về số thật của đơn sale khác.
    """
    visibility = phone_visibility_for(user)
    return [
        {
            "id": o.id,
            "name": o.name or "",
            "phone": visibility.apply(
                o.phone or "",
                o.staff_id,
                public_source=is_public_phone_source(o.order_source),
            ),
        }
        for o in orders
    ]


# GET trả 1 đơn; PATCH cập nhật inline trả đơn (kèm stock_warnings nếu có);
# DELETE soft-delete trả 204.
@extend_schema(
    request=OrderInlineUpdateSerializer,
    responses={
        200: OrderSerializer,
        204: OpenApiResponse(description="Đã xóa đơn"),
        403: OpenApiResponse(description="Không có quyền hoặc đơn đã hủy"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)

def _is_present_in_request(field: str, raw_keys: set) -> bool:
    """Field có được người dùng GỬI LÊN không (xét trên payload thô).

    Hạt `crm.<trường>` không bao giờ xuất hiện thành khoá riêng: cả khối CSKH đi
    chung một khoá `crm`. Tra thẳng tên hạt sẽ luôn trượt và guard câm lặng bỏ qua
    toàn bộ quyền CSKH — quyền vô hiệu mà test kiểu "có quyền thì sửa được" vẫn xanh.
    """
    if field.startswith(CRM_FIELD_PREFIX):
        return "crm" in raw_keys
    return field in raw_keys or VALIDATED_KEY_BY_FIELD.get(field, field) in raw_keys


# Schema khai OBJECT có chủ đích: body PATCH là subset ĐỘNG các field đơn theo
# field-policy per-role (không có serializer tĩnh nào mô tả đúng), response là row
# bản list. Thiếu khối này thì job openapi-contract đỏ vì spectacular không đoán
# được serializer của APIView function (audit 2026-08-19).
@extend_schema(methods=["GET"], responses={200: OpenApiResponse(description="Đơn hàng (row bản list)")})
@extend_schema(
    methods=["PATCH"],
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiResponse(description="Đơn hàng sau cập nhật (row bản list)")},
)
@extend_schema(methods=["DELETE"], responses={204: OpenApiResponse(description="Đã xoá mềm")})
# Sweep N2 giai đoạn 2: gate theo method thay require_section trần. "shipments"
# có mặt ở GET/PATCH (kho xem + cập nhật trạng thái giao của mọi đơn) nhưng KHÔNG
# có ở DELETE — mirror gate least-privilege bên trong (shipments-only không được
# xoá đơn; catalog "shipments" cũng không có :delete). Hành vi prod không đổi
# ngày deploy: migration users/0039 grandfather :edit/:delete cho role có :view.
@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([
    IsAuthenticated,
    require_method_permission(
        GET=tuple(f"{s}:view" for s in (*ORDER_SECTIONS, "shipments")),
        PATCH=(*ORDER_EDIT_CODES, "shipments:edit"),
        DELETE=ORDER_DELETE_CODES,
    ),
])
def update_order_inline(request, order_id: int):
    # Record-scope TRƯỚC mọi thứ: trước fix endpoint chỉ gate `can_edit_channel`, mà
    # `can_edit_order` trả True vô điều kiện cho scope OWN → một sale scope OWN
    # GET/PATCH/DELETE được đơn đơn lẻ của bất kỳ ai chỉ bằng cách đổi `order_id`
    # (audit 2026-07-26 N1). Giữ nguyên gate `can_edit_channel` bên dưới — chỉ siết thêm.
    order, error = scoped_order_or_error(
        request,
        order_id,
        INLINE_SCOPE_SECTIONS,
        select_related=("channel", "kiotviet_channel", "staff", "recall"),
        prefetch_related=("items",),
    )
    if error is not None:
        return error
    if not OrderService.can_edit_channel(request.user, order.channel.code):
        return Response({"detail": "Bạn không có quyền sửa đơn của kênh này."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        context = order_base_context(request.user, can_edit_fn=lambda _x: True)
        serializer = OrderSerializer(order, context=context)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == "DELETE":
        # Least-privilege: 'shipments'-only user chỉ được PATCH trạng thái CRM,
        # KHÔNG được xóa đơn. Yêu cầu giữ một trong các section đơn gốc.
        from apps.users.rbac import resolve_effective_permissions

        eff = resolve_effective_permissions(request.user)
        if not any(eff.can(f"{section}:view") for section in ORDER_SECTIONS):
            return Response(
                {"detail": "Bạn không có quyền xóa đơn hàng."},
                status=status.HTTP_403_FORBIDDEN,
            )
        order.deleted_at = timezone.now()
        order.save(update_fields=["deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    if order.invoice_status == 2 or order.status == 6:
        return Response({"detail": "Đơn đã hủy không thể chỉnh sửa."}, status=status.HTTP_403_FORBIDDEN)

    # Đổi sale = đổi người ăn doanh số → quyền riêng, không đi chung orders:edit.
    # So với giá trị hiện tại vì form đơn gửi lại full object: gửi đúng sale cũ
    # không phải là "đổi" và không được chặn oan.
    # Parse phải MÔ PHỎNG đúng cách OrderInlineUpdateSerializer.staff_id (PrimaryKeyRelatedField)
    # coerce giá trị — cuối cùng nó chỉ gọi int(value), không dùng isdigit(). Dùng isdigit() ở
    # đây từng cho phép " 7" (isdigit()=False) tuột qua thành None, trùng với đơn CHƯA có sale
    # (staff=None) và bỏ qua permission check hoàn toàn. Parse lỗi phải rơi vào sentinel KHÔNG
    # BAO GIỜ trùng staff_id thật (fail-closed) — không được map về None.
    if "staff_id" in request.data:
        raw_staff = request.data.get("staff_id")
        try:
            next_staff_id = int(raw_staff) if raw_staff is not None else None
        except (TypeError, ValueError):
            next_staff_id = object()
        if next_staff_id != order.staff_id and not can_assign_sale(request.user):
            return Response(
                {"detail": "Bạn không có quyền đổi sale phụ trách đơn."},
                status=status.HTTP_403_FORBIDDEN,
            )

    serializer = OrderInlineUpdateSerializer(
        instance=order,
        data=request.data,
        partial=True,
        context=order_base_context(request.user),
    )
    serializer.is_valid(raise_exception=True)

    # Đơn đã có mã hoá đơn = đã chốt hoá đơn trên KiotViet → khoá hai khối SẢN PHẨM và
    # TÀI CHÍNH. Các thông tin khác vẫn sửa được vì hay phải vá sau khi đơn đã đi. Guard
    # đặt ở view nên chỉ chặn hành vi NGƯỜI DÙNG: job đẩy KiotViet ghi invoice_code, job
    # KShip ghi shipping_code, ingest landing và `dispatch_orders_batch` (quét kho) đi qua
    # service nên không bị chặn — chủ ý, xem docs/invoice-code-edit-lock.md.
    #
    # Chặn sau `is_valid()` để so trên `validated_data` (đã coerce type), và so GIÁ TRỊ
    # chứ không so sự có mặt của field: form đơn gửi lại full object nên chặn theo sự có
    # mặt sẽ 403 oan cho các cập nhật khác đi kèm (cùng khuôn guard `staff_id` ở trên).
    if is_invoice_locked(order):
        locked_changes = locked_field_changes(order, serializer.validated_data)
        if locked_changes:
            return Response(
                {
                    "detail": (
                        "Đơn đã có mã hóa đơn nên không sửa được: "
                        f"{describe_locked_fields(locked_changes)}."
                    ),
                    "locked_fields": locked_changes,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

    # Quyền sửa theo field (allow-list, fail-closed). Đặt sau is_valid() để so trên
    # validated_data đã coerce type, và so GIÁ TRỊ chứ không so sự có mặt của field:
    # form đơn gửi lại full object nên chặn theo sự có mặt sẽ 403 oan mọi lần lưu.
    # Cùng khuôn hai guard ngay trên (staff_id, invoice_lock).
    #
    # Guard đặt ở view nên chỉ chặn hành vi NGƯỜI DÙNG: job nền, sync KiotViet, quét
    # kho đi qua service nên không bị chặn — chủ ý, xem spec mục "Ngoài phạm vi".
    # Chỉ xét field NGƯỜI DÙNG thật sự gửi lên. `validated_data` còn chứa field
    # serializer TỰ SINH: `validate()` ghi đè province/district/ward bằng giá trị chuẩn
    # hoá từ *_code (và blank district_code/ward_code khi chỉ gửi province_code),
    # `validate_phone` chuẩn hoá số, `branch_id` được normalize. FE strip được key user
    # gửi, KHÔNG strip được key serializer thêm vào — role có `province_code` mà không
    # có `province` sẽ ăn 403 "không có quyền sửa Tỉnh/TP" mỗi lần lưu, dù không đụng
    # địa chỉ (rất phổ biến: đơn KiotViet/landing/import có tên tỉnh khác tên chuẩn hoá
    # từ mã, đơn có tên mà không có mã thì giá trị chuẩn hoá là chuỗi rỗng).
    #
    # Không hở: field gửi thẳng vẫn bị chặn y như cũ; field dẫn xuất chỉ đổi được khi
    # user chủ động sửa field nguồn mà họ ĐƯỢC cấp.
    raw_keys = set(request.data) if isinstance(request.data, dict) else set()
    editable = order_editable_fields(request.user)
    ungranted = sorted(
        f
        for f in ALL_ORDER_FIELDS
        if f not in editable
        and _is_present_in_request(f, raw_keys)
    )
    blocked = changed_fields(order, serializer.validated_data, ungranted)
    if blocked:
        logger.warning(
            "field_grant blocked user=%s order=%s fields=%s",
            request.user.id, order.id, blocked,
        )
        return Response(
            {
                "detail": (
                    "Bạn không có quyền sửa: "
                    f"{', '.join(ORDER_FIELD_LABELS.get(f, f) for f in blocked)}."
                ),
                "blocked_fields": blocked,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        # _verified: xác minh KiotViet (HTTP) chạy TRƯỚC transaction ghi sổ — không để
        # một round-trip mạng giữ transaction mở mỗi lần user bấm "Đã xuất kho".
        warnings = OrderService.update_order_inline_verified(
            order, serializer.validated_data, request.user
        )
    # AssessmentValidationError mang message chỉ đích danh field hỏng — trả thẳng
    # cho client. Gộp nó vào nhánh ValueError chung chung bên dưới từng làm sự cố
    # inline-edit 400 chiều 2026-08-03 mất thêm một vòng chẩn đoán vì response chỉ
    # nói "Dữ liệu không hợp lệ." và log không có gì.
    except AssessmentValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except ValueError:
        return Response({"detail": "Dữ liệu không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    context = order_base_context(request.user, can_edit_fn=lambda _x: True)
    data = OrderSerializer(order, context=context).data
    if warnings:
        data = {**data, "stock_warnings": warnings}
    return Response(data)


@extend_schema(
    request=None,
    responses={
        201: OrderSerializer,
        403: OpenApiResponse(description="Không có quyền nhân bản đơn của kênh này"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_CREATE_CODES)])
def duplicate_order(request, order_id: int):
    # Nhân bản là hành vi GHI trên nội dung đơn nguồn (copy khách/sản phẩm/giá) nên phải
    # mirror read-scope: không được nhân bản đơn mà mình không có quyền xem.
    order, error = scoped_order_or_error(
        request,
        order_id,
        ORDER_WRITE_SCOPE_SECTIONS,
        select_related=("channel", "kiotviet_channel", "staff", "customer"),
        prefetch_related=("items__product",),
    )
    if error is not None:
        return error
    if not OrderService.can_edit_channel(request.user, order.channel.code):
        return Response({"detail": "Bạn không có quyền nhân bản đơn của kênh này."}, status=status.HTTP_403_FORBIDDEN)

    duplicated_order = OrderService.duplicate_order(order, request.user)
    context = order_base_context(request.user, can_edit_fn=lambda _x: True)
    return Response(
        OrderSerializer(duplicated_order, context=context).data,
        status=status.HTTP_201_CREATED,
    )


@extend_schema(
    request=OrderBulkDeleteSerializer,
    responses={
        200: inline_serializer(
            "BulkDeleteOrdersResponse",
            fields={"deleted_count": drf_serializers.IntegerField()},
        ),
        403: OpenApiResponse(description="Không có quyền xóa một số đơn"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_DELETE_CODES)])
def bulk_delete_orders(request):
    serializer = OrderBulkDeleteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    ids = serializer.validated_data["ids"]

    # Record-scope TRƯỚC can_edit_channel: đơn ngoài scope bị loại khỏi queryset nên
    # `deleted_count` chỉ đếm đơn user thật sự được xoá (id ngoài scope im lặng bỏ qua,
    # đúng như id không tồn tại — không tiết lộ đơn đó có thật hay không).
    orders = list(
        scoped_orders_queryset(
            request.user, ORDER_WRITE_SCOPE_SECTIONS, select_related=("channel",)
        ).filter(id__in=ids)
    )
    forbidden_channels = sorted(
        {order.channel.code for order in orders if not OrderService.can_edit_channel(request.user, order.channel.code)}
    )
    if forbidden_channels:
        return Response(
            {"detail": "Bạn không có quyền xóa một số đơn.", "channels": forbidden_channels},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not orders:
        return Response({"deleted_count": 0}, status=status.HTTP_200_OK)

    order_ids = [order.id for order in orders]
    now = timezone.now()
    deleted_count = Order.objects.filter(id__in=order_ids, deleted_at__isnull=True).update(
        deleted_at=now, updated_at=now
    )
    # Soft delete bằng queryset.update bypass signal → tự recompute loại khách.
    refresh_customer_type({order.customer_id for order in orders})
    return Response({"deleted_count": deleted_count}, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "TransferLandingOrdersRequest",
        fields={"ids": drf_serializers.ListField(child=drf_serializers.IntegerField())},
    ),
    responses={
        200: inline_serializer(
            "TransferLandingOrdersResponse",
            fields={"transferred_count": drf_serializers.IntegerField()},
        ),
        400: OpenApiResponse(description="Danh sách không hợp lệ hoặc đơn chưa có kênh KiotViet"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("landing-orders:edit")])
def transfer_landing_orders(request):
    """Chuyển đơn Landing sang trang Đơn hàng. Điều kiện: kiotviet_channel_id != null."""
    ids = request.data.get("ids", [])
    if not ids or not isinstance(ids, list):
        return Response({"detail": "Danh sách đơn hàng không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

    # Endpoint này TRƯỚC ĐÂY không có gate bản ghi nào: chỉ cần quyền
    # landing-orders:view là chuyển được đơn của bất kỳ ai sang trang Đơn hàng.
    queryset = scoped_orders_queryset(
        request.user,
        _LANDING_SCOPE_SECTIONS,
        select_related=("channel", "kiotviet_channel"),
    ).filter(id__in=ids)

    # Lô còn trong trần đính row (xem `include_rows` bên dưới) thì nạp sẵn MỌI quan hệ
    # OrderListSerializer đọc, để `build_order_created_row` từng đơn không lazy-load
    # per-row (~8 query × 50 đơn ≈ 400 query/POST — audit performance 2026-08-19,
    # P0.1). Gate theo `len(ids)` vì phải quyết TRƯỚC khi chạm DB; orders ⊆ ids nên
    # lô trong trần chắc chắn được nạp đủ. Lô quá trần không phát row → khỏi kéo
    # assessment/items của hàng trăm đơn vô ích.
    if len(ids) <= _TRANSFER_ROW_PAYLOAD_MAX:
        from django.db.models import Prefetch

        from apps.orders.models import OrderNeed

        queryset = queryset.select_related(
            "customer", "staff", "assessment", "assessment__updated_by", "recall"
        ).prefetch_related(
            "items",
            "recall_entries",
            Prefetch(
                "assessment__needs",
                queryset=OrderNeed.objects.select_related("need_type__group").order_by("id"),
            ),
        )

    orders = list(queryset)

    if not orders:
        return Response({"detail": "Không tìm thấy đơn hàng."}, status=status.HTTP_404_NOT_FOUND)

    # Chỉ trả `id`: payload 400 này trước đây kèm `name` + `phone` của khách, tức chỉ
    # cần quyền landing-orders:view là dò được PII đơn bất kỳ qua vòng lặp `ids`.
    # FE (OrdersTable) chỉ cần id để hiện "#123" trong toast.
    invalid_orders = [{"id": order.id} for order in orders if order.kiotviet_channel_id is None]

    if invalid_orders:
        return Response(
            {
                "detail": "Một số đơn chưa có kênh KiotViet, không thể chuyển.",
                "invalid_orders": invalid_orders,
                "invalid_count": len(invalid_orders),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    from apps.orders.application.services.order_service_commands import OrderServiceCommands

    from apps.orders.application.services.order_event_publisher import (
        build_order_created_row,
        build_order_rows_context,
    )

    transferable_ids = [order.id for order in orders if not order.show_orders]
    if transferable_ids:
        Order.objects.filter(id__in=transferable_ids).update(show_orders=True)

    # Đây là đường REST-fanout CUỐI CÙNG còn sót của trang Đơn hàng (đo 2026-08-11,
    # §6.5 của docs/cache-latency-optimization-2026-08-11.md). Đơn vừa chuyển sang
    # section `orders` nên tab đang mở KHÔNG có dòng nào để vá tại chỗ — nó buộc
    # phải đi lấy. Mà chuyển đơn là thao tác HÀNG LOẠT: M đơn gom vào coalescer của
    # FE, ≥2 id là mỗi tab bắn một POST /api/orders (list đầy đủ). M đơn × N tab
    # sinh ra đúng chữ ký đã đo được: 8-10 request list dồn trong cùng một giây,
    # mỗi cái ~460ms server. Mang sẵn row thì con số đó về 0.
    #
    # Trần _TRANSFER_ROW_PAYLOAD_MAX: với lô rất lớn thì phép đổi lật ngược dấu —
    # serialize 500 row ở điểm phát (~10ms/row) đắt hơn hẳn một lần refetch danh
    # sách của mỗi tab (coalescer gom cả lô thành MỘT request). Quá trần thì trả
    # row=None và FE tự rơi về đường fetch cũ.
    include_rows = len(orders) <= _TRANSFER_ROW_PAYLOAD_MAX

    # Map dùng chung (cảnh báo trùng, reminder) dựng MỘT lần cho cả lô rồi truyền
    # vào serializer từng row — không để mỗi row tự query lại (P0.1 audit 2026-08-19).
    row_context = build_order_rows_context(orders) if include_rows else None

    for order in orders:
        # `show_orders` vừa được set bằng queryset `.update()` nên instance trong
        # bộ nhớ vẫn mang giá trị CŨ. Không đồng bộ lại thì row phát đi mang
        # `show_orders=false` và FE lọc nó ra khỏi bảng ngay sau khi upsert — đơn
        # vừa chuyển sẽ "chuyển xong mà không thấy đâu".
        order.show_orders = True
        event_payload = OrderEventContract.build_order_updated(
            order_id=order.id,
            channel_code=order.channel.code if order.channel else "",
            staff_id=order.staff_id,
            status=order.status,
            source="orders.service.transfer_landing_orders",
            row=build_order_created_row(order, context=row_context) if include_rows else None,
        )
        OrderServiceCommands.push_event(event_payload)

    return Response({"transferred_count": len(orders)}, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "RestoreCancelledOrdersRequest",
        fields={
            "ids": drf_serializers.ListField(child=drf_serializers.IntegerField()),
            "restore_show_orders": drf_serializers.BooleanField(required=False),
        },
    ),
    responses={
        202: inline_serializer(
            "RestoreCancelledOrdersResponse",
            fields={
                "queued_count": drf_serializers.IntegerField(),
                "queued_ids": drf_serializers.ListField(child=drf_serializers.IntegerField()),
                "skipped_count": drf_serializers.IntegerField(),
                "skipped_ids": drf_serializers.ListField(child=drf_serializers.IntegerField()),
            },
        ),
        400: OpenApiResponse(description="Danh sách đơn hàng không hợp lệ"),
        403: OpenApiResponse(description="Không có quyền khôi phục một số đơn"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
        500: OpenApiResponse(description="Không đẩy được job khôi phục vào queue"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_EDIT_CODES)])
def restore_cancelled_orders(request):
    """Enqueue task tạo đơn mới (clone) từ đơn đã hủy; đơn cũ giữ nguyên làm lịch sử.

    Worker bắn 'restore.completed' khi xong batch."""
    ids = request.data.get("ids", [])
    restore_show_orders_raw = request.data.get("restore_show_orders", False)
    restore_show_orders = (
        restore_show_orders_raw is True
        or str(restore_show_orders_raw).strip().lower() in {"1", "true", "yes", "on"}
    )
    if not isinstance(ids, list) or not ids:
        return Response({"detail": "Danh sách đơn hàng không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

    # Id ngoài scope rơi vào `skipped_ids` như id không tồn tại — worker chỉ nhận
    # `valid_ids` nên không đơn nào ngoài phạm vi bị nhân bản (clone) thành đơn mới.
    orders = list(
        scoped_orders_queryset(
            request.user, ORDER_WRITE_SCOPE_SECTIONS, select_related=("channel",)
        ).filter(id__in=ids)
    )
    if not orders:
        return Response({"detail": "Không tìm thấy đơn hàng."}, status=status.HTTP_404_NOT_FOUND)

    forbidden_channels = sorted(
        {order.channel.code for order in orders if not OrderService.can_edit_channel(request.user, order.channel.code)}
    )
    if forbidden_channels:
        return Response(
            {"detail": "Bạn không có quyền khôi phục một số đơn.", "channels": forbidden_channels},
            status=status.HTTP_403_FORBIDDEN,
        )

    valid_ids = [o.id for o in orders]
    skipped_ids = [oid for oid in ids if oid not in set(valid_ids)]

    try:
        dispatch(
            "orders.restore_orders",
            queue="orders",
            order_ids=valid_ids,
            user_id=request.user.id,
            restore_show_orders=restore_show_orders,
        )
    except Exception:
        logger.exception("Failed to dispatch process_restore_orders task ids=%s", valid_ids)
        return Response(
            {"detail": "Không thể đẩy job khôi phục vào queue. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            "queued_count": len(valid_ids),
            "queued_ids": valid_ids,
            "skipped_count": len(skipped_ids),
            "skipped_ids": skipped_ids,
        },
        status=status.HTTP_202_ACCEPTED,
    )


@extend_schema(
    request=inline_serializer(
        "CancelKiotVietOrdersRequest",
        fields={"ids": drf_serializers.ListField(child=drf_serializers.IntegerField())},
    ),
    responses={
        202: inline_serializer(
            "CancelKiotVietOrdersResponse",
            fields={
                "queued_count": drf_serializers.IntegerField(),
                "skipped_count": drf_serializers.IntegerField(),
                "skipped_orders": inline_serializer(
                    "CancelKiotVietSkippedOrder",
                    many=True,
                    fields={
                        "id": drf_serializers.IntegerField(),
                        "name": drf_serializers.CharField(allow_blank=True),
                        "phone": drf_serializers.CharField(allow_blank=True),
                    },
                ),
                "dispatch_failed_count": drf_serializers.IntegerField(),
                "already_processing_count": drf_serializers.IntegerField(),
            },
        ),
        400: OpenApiResponse(description="Danh sách không hợp lệ hoặc không có đơn có mã hóa đơn"),
        403: OpenApiResponse(description="Không có quyền thao tác một số đơn"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_EDIT_CODES)])
def cancel_kiotviet_orders(request):
    """Hủy đơn trên KiotViet (xóa hóa đơn). Chỉ xử lý đơn có invoice_code. Chạy qua queue kiotviet."""
    ids = request.data.get("ids", [])
    if not ids or not isinstance(ids, list):
        return Response({"detail": "Danh sách đơn hàng không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

    orders = list(
        scoped_orders_queryset(
            request.user,
            ORDER_WRITE_SCOPE_SECTIONS,
            select_related=("channel", "kiotviet_channel"),
        ).filter(id__in=ids)
    )

    if not orders:
        return Response({"detail": "Không tìm thấy đơn hàng."}, status=status.HTTP_404_NOT_FOUND)

    forbidden_channels = sorted(
        {order.channel.code for order in orders if not OrderService.can_edit_channel(request.user, order.channel.code)}
    )
    if forbidden_channels:
        return Response(
            {"detail": "Bạn không có quyền thao tác một số đơn.", "channels": forbidden_channels},
            status=status.HTTP_403_FORBIDDEN,
        )

    orders_with_invoice = [o for o in orders if o.invoice_code and o.invoice_id and o.kiotviet_channel]
    orders_without_invoice = [
        o for o in orders if not o.invoice_code or not o.invoice_id or not o.kiotviet_channel
    ]

    if not orders_with_invoice:
        skipped = _skipped_orders_payload(request.user, orders_without_invoice)
        return Response(
            {
                "detail": "Không có đơn nào có mã hóa đơn để hủy.",
                "skipped_orders": skipped,
                "skipped_count": len(skipped),
                "queued_count": 0,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    job_action = OrderKiotVietJob.ACTION_CANCEL_INVOICE
    order_ids = [o.id for o in orders_with_invoice]
    result = OrderKiotVietJobService.enqueue(
        order_ids,
        job_action,
        request.user,
        event_source="orders.views.cancel_kiotviet_queued",
    )

    skipped = _skipped_orders_payload(request.user, orders_without_invoice)

    return Response(
        {
            "queued_count": result.queued_count,
            "skipped_count": len(skipped),
            "skipped_orders": skipped,
            "dispatch_failed_count": result.dispatch_failed_count,
            "already_processing_count": len(orders_with_invoice) - result.created_count,
        },
        status=status.HTTP_202_ACCEPTED,
    )


# step=precheck trả 200 với precheck_payload (các key bổ sung tuỳ action);
# step=execute trả 202 với số liệu enqueue. Khai báo các key ổn định chung.
@extend_schema(
    request=OrderKiotVietActionSerializer,
    responses={
        200: inline_serializer(
            "RunKiotVietActionPrecheckResponse",
            fields={
                "action": drf_serializers.CharField(),
                "step": drf_serializers.CharField(),
                "input_count": drf_serializers.IntegerField(),
                "target_count": drf_serializers.IntegerField(),
            },
        ),
        202: inline_serializer(
            "RunKiotVietActionExecuteResponse",
            fields={
                "action": drf_serializers.CharField(),
                "step": drf_serializers.CharField(),
                "input_count": drf_serializers.IntegerField(),
                "target_count": drf_serializers.IntegerField(),
                "queued_count": drf_serializers.IntegerField(),
                "dispatch_failed_count": drf_serializers.IntegerField(),
                "already_processing_count": drf_serializers.IntegerField(),
            },
        ),
        400: OpenApiResponse(description="Đơn thiếu/lệch kênh KiotViet"),
        403: OpenApiResponse(description="Không có quyền thao tác một số đơn"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_EDIT_CODES)])
def run_kiotviet_action(request):
    serializer = OrderKiotVietActionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    action = serializer.validated_data["action"]
    step = serializer.validated_data["step"]
    ids = serializer.validated_data.get("ids", [])
    order_ids_payload = serializer.validated_data.get("order_ids", [])
    selected_ids = order_ids_payload or ids
    is_push_action = action in {
        OrderKiotVietActionSerializer.ACTION_PUSH_SELECTED,
        OrderKiotVietActionSerializer.ACTION_PUSH_UNSENT,
    }

    # Record-scope áp cho CẢ HAI nhánh queryset. Nhánh chạy theo filter (push_unsent /
    # sync_status_has_shipping) nguy hiểm nhất: không có `ids` nào để giới hạn, nên
    # trước fix một sale scope OWN bấm "đẩy tất cả đơn chưa gửi" là đẩy đơn của TOÀN
    # công ty lên KiotViet. Sau fix, "tất cả" = tất cả trong phạm vi được cấp.
    scoped_orders = scoped_orders_queryset(
        request.user, ORDER_WRITE_SCOPE_SECTIONS, select_related=("channel", "kiotviet_channel")
    )

    if step == OrderKiotVietActionSerializer.STEP_PRECHECK:
        orders = scoped_orders
        if action == OrderKiotVietActionSerializer.ACTION_PUSH_SELECTED:
            orders = orders.filter(id__in=ids)
        else:
            orders = orders.filter(order_code="")

        order_list = list(orders)
        forbidden_channels = sorted(
            {order.channel.code for order in order_list if not OrderService.can_edit_channel(request.user, order.channel.code)}
        )
        if forbidden_channels:
            return Response(
                {"detail": "Bạn không có quyền thao tác một số đơn.", "channels": forbidden_channels},
                status=status.HTTP_403_FORBIDDEN,
            )

        precheck_payload = build_kiotviet_push_precheck(orders)
        return Response(
            {
                "action": action,
                "step": step,
                "input_count": len(ids),
                "target_count": len(order_list),
                **precheck_payload,
            },
            status=status.HTTP_200_OK,
        )

    orders = scoped_orders

    if action in {
        OrderKiotVietActionSerializer.ACTION_PUSH_SELECTED,
        OrderKiotVietActionSerializer.ACTION_SYNC_SHIPPING_SELECTED,
        OrderKiotVietActionSerializer.ACTION_SYNC_STATUS_SELECTED,
    } or action in _QUICK_SALE_ACTIONS:
        orders = orders.filter(id__in=selected_ids)

    if action == OrderKiotVietActionSerializer.ACTION_PUSH_UNSENT:
        if is_push_action and selected_ids:
            orders = orders.filter(id__in=selected_ids)
        else:
            orders = orders.filter(order_code="")
    elif action == OrderKiotVietActionSerializer.ACTION_SYNC_STATUS_SELECTED:
        # Sync trạng thái chỉ cần invoice_code, không bắt buộc shipping_code.
        orders = orders.exclude(invoice_code="")
    elif action == OrderKiotVietActionSerializer.ACTION_SYNC_STATUS_HAS_SHIPPING:
        orders = orders.exclude(shipping_code="").exclude(invoice_code="")

    order_list = list(orders)
    forbidden_channels = sorted(
        {order.channel.code for order in order_list if not OrderService.can_edit_channel(request.user, order.channel.code)}
    )
    if forbidden_channels:
        return Response(
            {"detail": "Bạn không có quyền thao tác một số đơn.", "channels": forbidden_channels},
            status=status.HTTP_403_FORBIDDEN,
        )

    if action == OrderKiotVietActionSerializer.ACTION_SYNC_SHIPPING_SELECTED:
        if any(order.kiotviet_channel_id is None for order in order_list):
            return Response({"detail": "Đơn hàng chưa được gán kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)
        kiotviet_channel_ids = {order.kiotviet_channel_id for order in order_list}
        if len(kiotviet_channel_ids) > 1:
            return Response(
                {"detail": "Vui lòng chọn các đơn thuộc cùng một kênh KiotViet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if action in _QUICK_SALE_ACTIONS:
        if any(order.kiotviet_channel_id is None for order in order_list):
            return Response({"detail": "Đơn hàng chưa được gán kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)
        # Bán trực tiếp trừ tồn ngay theo kho xuất của đơn. Thiếu kho mà vẫn cho chạy
        # thì hoá đơn KiotViet được tạo trong khi tồn không trừ — nghiệp vụ coi như
        # xong còn kho sai âm thầm. Chặn TRƯỚC khi tạo job, không phải sau.
        orders_without_warehouse = [
            order.id for order in order_list if not order.fulfillment_warehouse_id
        ]
        if orders_without_warehouse:
            return Response(
                {
                    "detail": "Đơn bán trực tiếp phải có kho xuất. Hãy chọn kho xuất trước khi tạo nhanh.",
                    "orders_without_warehouse": orders_without_warehouse,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Đơn đã có order_code là đơn đã đẩy đi vận chuyển (chờ kho quét xuất theo
        # đường shipment) — quick-sale chọn nhầm vào đây sẽ lật nhãn sang direct và
        # ép "dispatched" TRƯỚC KHI hàng thật sự rời kho theo đường đó.
        orders_already_pushed = [order.id for order in order_list if str(order.order_code or "").strip()]
        if orders_already_pushed:
            return Response(
                {
                    "detail": "Đơn đã đẩy đi vận chuyển thì không dùng chức năng tạo nhanh.",
                    "orders_already_pushed": orders_already_pushed,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Quick sale: chỉ chuyển sang "Giao thành công" SAU khi tạo hóa đơn KV
        # thành công, không set eager ở đây để tránh sai trạng thái.

        # Ghi cờ ngay sau khi cả 3 guard trên đã qua, KHÔNG phụ thuộc enum action: từ
        # 2026-08-28 mọi quick-sale đều là "xuất sau", đường trừ tồn ngay đã bị gỡ. Vẫn
        # ghi TƯỜNG MINH (không dựa vào default của cột) vì đơn có thể mang cờ cũ từ lần
        # bấm trước, và vì đây là chỗ duy nhất quyết định luật cho đơn mới.
        Order.objects.filter(id__in=[order.id for order in order_list]).update(
            direct_dispatch_deferred=True
        )

    if action in {
        OrderKiotVietActionSerializer.ACTION_PUSH_SELECTED,
        OrderKiotVietActionSerializer.ACTION_PUSH_UNSENT,
    }:
        job_action = OrderKiotVietJob.ACTION_PUSH
    elif action in {OrderKiotVietActionSerializer.ACTION_SYNC_SHIPPING_SELECTED}:
        job_action = OrderKiotVietJob.ACTION_SYNC_SHIPPING
    elif action in _QUICK_SALE_ACTIONS:
        job_action = OrderKiotVietJob.ACTION_CREATE_INVOICE
    else:
        job_action = OrderKiotVietJob.ACTION_SYNC_STATUS

    order_ids = [order.id for order in order_list]
    result = OrderKiotVietJobService.enqueue(
        order_ids,
        job_action,
        request.user,
        event_source="orders.views.kiotviet_action_queued",
    )

    return Response(
        {
            "action": action,
            "step": OrderKiotVietActionSerializer.STEP_EXECUTE,
            "input_count": len(selected_ids),
            "target_count": len(order_list),
            "queued_count": result.queued_count,
            "dispatch_failed_count": result.dispatch_failed_count,
            "already_processing_count": len(order_list) - result.created_count,
        },
        status=status.HTTP_202_ACCEPTED,
    )
