import logging
import time

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import (
    ORDER_CREATE_CODES,
    ORDER_EDIT_CODES,
    ORDER_SECTIONS,
    can_assign_sale,
    require_permission,
    require_section,
)

from apps.orders.contracts.kiotviet_order_flow_port import kiotviet_order_flow_port

from apps.orders.application.services.order_kiotviet_job_service import OrderKiotVietJobService
from apps.orders.application.services.sale_reminder_service import (
    get_next_reminder_at_map,
)
from apps.orders.crm_catalog import build_crm_catalog_payload
from apps.orders.models import KshipSenderAddress, OrderKiotVietJob
from apps.orders.repositories import OrderRepository
from apps.orders.serializers import (
    OrderCreateKshipDeliverySerializer,
    OrderCreateSerializer,
    OrderKiotVietJobSerializer,
    OrderKshipOptionsSerializer,
    OrderSerializer,
)
from apps.orders.application.services.order_service_impl import OrderService

from ._view_helpers import (
    ORDER_WRITE_SCOPE_SECTIONS,
    _normalize_phone,
    order_base_context,
    order_record_scope_q,
    scoped_orders_queryset,
)

logger = logging.getLogger(__name__)


# Cầu sang KiotViet qua port (Lô 2): view không import service/model của app
# kiotviet. Giữ nguyên TÊN hàm module-level để test hiện có patch
# `apps.orders.transport.views.order_views_impl.order_crud_view.<tên>` tiếp tục ăn.
def get_kship_providers_and_services(channel):
    return kiotviet_order_flow_port().get_kship_options(channel)


@extend_schema(responses={200: OrderSerializer(many=True)})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def list_order_history(request):
    phone = _normalize_phone(request.query_params.get("phone", ""))
    if not phone:
        return Response([], status=status.HTTP_200_OK)

    # Record-scope: `?phone=` là tham số THUẦN do client chọn, không ràng buộc gì với đơn
    # user được xem — trước fix chỉ cần `orders:view` là đọc được TOÀN BỘ lịch sử đơn
    # (kèm PII khách + CRM) của bất kỳ số điện thoại nào (audit 2026-07-26 N1, phía ĐỌC).
    # Lọc ở tầng view thay vì đổi chữ ký repository: `list_history_by_phone` trả queryset
    # và chỉ có duy nhất call-site này, nên AND thêm Q là đủ và không lan ra chỗ khác.
    # Cắt 200 đơn mới nhất (queryset đã order_by -created_at) ngay tại tầng view:
    # SĐT trùng nhiều đơn thì payload/serialize không có trần. Không đổi repository
    # (audit 2026-07-28 DB-7; phân trang thật xem docs/audit.md BE-3).
    orders = list(
        OrderRepository.list_history_by_phone(phone).filter(
            order_record_scope_q(request.user, ORDER_WRITE_SCOPE_SECTIONS)
        )[:200]
    )

    def can_edit(channel_code: str) -> bool:
        return OrderService.can_edit_channel(request.user, channel_code)

    # Batch next-reminder cho cả trang (1 query) thay vì 1 query/đơn khi serialize —
    # cùng khuôn `_build_order_list_context` của list đơn chính (audit 2026-07-28 DB-7).
    context = order_base_context(
        request.user,
        can_edit_fn=can_edit,
        next_reminder_map=get_next_reminder_at_map(o.id for o in orders),
    )
    serializer = OrderSerializer(orders, many=True, context=context)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    request=OrderCreateSerializer,
    responses={
        201: OrderSerializer,
        400: OpenApiResponse(description="Dữ liệu không hợp lệ"),
        403: OpenApiResponse(description="Không có quyền tạo đơn cho kênh này"),
    },
)
# Sweep N2 giai đoạn 2: form tạo đơn dùng chung cả 3 trang đơn (section nguồn của
# đơn tạo ra do payload quyết định — order_source/channel), nên gate any-of
# :create như quy ước ORDER_WRITE_SCOPE_SECTIONS. Grandfather 0039 giữ hành vi.
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_CREATE_CODES)])
def create_order(request):
    # create-timing (cùng họ landing-timing): p95 POST /api/orders = 3.6s nhưng
    # slow-log Postgres trống — nghĩa là chi phí rải ở nhiều bước, không nằm trong
    # một query. Log này bổ dọc request để prod tự chỉ chỗ đau, thay vì đoán.
    t0 = time.monotonic()
    serializer = OrderCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data
    channel = payload["channel"]
    if not OrderService.can_edit_channel(request.user, channel.code):
        return Response({"detail": "Bạn không có quyền tạo đơn cho kênh này."}, status=status.HTTP_403_FORBIDDEN)

    # Không có quyền gán sale → đơn luôn về tên chính mình. Chặn cứng sẽ làm sale
    # không tạo nổi đơn cho bản thân; ép về mình vẫn đóng đường chuyển đơn trá hình
    # qua bước tạo.
    if not can_assign_sale(request.user):
        requested_staff = payload.get("staff")
        if requested_staff is not None and requested_staff.id != request.user.id:
            return Response(
                {"detail": "Bạn chỉ được tạo đơn cho chính mình."},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Cờ chia tự động vẫn mở cho người thiếu quyền (quyết định sản phẩm 17/08):
        # họ không CHỌN được ai, chỉ để bộ chia chọn theo trọng số. Ép staff về
        # chính mình ở đây sẽ vô hiệu hoá cờ vì `assign_if_unassigned` return sớm
        # khi đơn đã có staff. Nhánh 403 ngay trên vẫn chặn chỉ định đích danh.
        if not payload.get("auto_assign_staff"):
            payload["staff"] = request.user
    t1 = time.monotonic()

    try:
        order = OrderService.create_order_manual(payload, request.user)
    except ValueError:
        return Response({"detail": "Dữ liệu không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    t2 = time.monotonic()

    context = order_base_context(request.user, can_edit_fn=lambda _x: True)
    data = OrderSerializer(order, context=context).data
    logger.info(
        "create-timing validate=%.0fms service=%.0fms serialize=%.0fms total=%.0fms order_id=%s",
        (t1 - t0) * 1000,
        (t2 - t1) * 1000,
        (time.monotonic() - t2) * 1000,
        (time.monotonic() - t0) * 1000,
        order.id,
    )
    return Response(data, status=status.HTTP_201_CREATED)


@extend_schema(
    responses={
        200: inline_serializer(
            "KiotVietChannelOption",
            many=True,
            fields={
                "id": drf_serializers.IntegerField(),
                "name": drf_serializers.CharField(),
                "branch_id": drf_serializers.IntegerField(allow_null=True),
                "branch_ids": drf_serializers.JSONField(),
                "branch_names": drf_serializers.JSONField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def list_kiotviet_options(request):
    from ._view_helpers import _parse_required_pagination

    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    data = kiotviet_order_flow_port().list_channel_options(
        offset=pagination["offset"], limit=pagination["limit"]
    )
    return Response(data)


@extend_schema(
    request=OrderKshipOptionsSerializer,
    responses={
        200: inline_serializer(
            "KshipOptionsResponse",
            fields={
                "channel_id": drf_serializers.IntegerField(),
                "channel_name": drf_serializers.CharField(),
                # providers/services: cấu trúc do KShip setting-service trả về.
                "providers": drf_serializers.ListField(child=drf_serializers.DictField()),
                "services": drf_serializers.ListField(child=drf_serializers.DictField()),
            },
        ),
        400: OpenApiResponse(description="Đơn thiếu kênh KiotViet hoặc cấu hình KShip"),
        403: OpenApiResponse(description="Không có quyền thao tác một số đơn"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng hợp lệ"),
        502: OpenApiResponse(description="Không lấy được danh sách KShip"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def get_kship_options(request):
    serializer = OrderKshipOptionsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    ids = serializer.validated_data["ids"]

    # Record-scope: id ngoài phạm vi được cấp bị loại khỏi queryset → 404 như id không
    # tồn tại, và KHÔNG phát sinh round-trip sang KShip cho đơn của người khác.
    orders = list(
        scoped_orders_queryset(
            request.user,
            ORDER_WRITE_SCOPE_SECTIONS,
            select_related=("channel", "kiotviet_channel"),
        ).filter(id__in=ids)
    )
    if not orders:
        return Response({"detail": "Không tìm thấy đơn hàng hợp lệ."}, status=status.HTTP_404_NOT_FOUND)

    forbidden_channels = sorted(
        {order.channel.code for order in orders if not OrderService.can_edit_channel(request.user, order.channel.code)}
    )
    if forbidden_channels:
        return Response(
            {"detail": "Bạn không có quyền thao tác một số đơn.", "channels": forbidden_channels},
            status=status.HTTP_403_FORBIDDEN,
        )

    missing_kiotviet_channel = any(order.kiotviet_channel_id is None for order in orders)
    if missing_kiotviet_channel:
        return Response({"detail": "Đơn hàng chưa được gán kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)

    channel_ids = {order.kiotviet_channel_id for order in orders}
    if len(channel_ids) > 1:
        return Response({"detail": "Vui lòng chọn các đơn thuộc cùng một kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)

    kiotviet_channel = orders[0].kiotviet_channel
    if kiotviet_channel is None:
        return Response({"detail": "Không tìm thấy kênh KiotViet của đơn hàng."}, status=status.HTTP_400_BAD_REQUEST)

    if not str(kiotviet_channel.username or "").strip() or not str(kiotviet_channel.password or "").strip():
        return Response(
            {"detail": "Kênh KiotViet chưa cấu hình username/password cho luồng KShip."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        options = get_kship_providers_and_services(kiotviet_channel)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        logger.exception("Failed to load KShip options for channel_id=%s", kiotviet_channel.id)
        return Response({"detail": f"Không thể lấy danh sách KShip: {str(exc)}"}, status=status.HTTP_502_BAD_GATEWAY)

    return Response(
        {
            "channel_id": kiotviet_channel.id,
            "channel_name": kiotviet_channel.name,
            **options,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(responses={200: OrderKiotVietJobSerializer(many=True)})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def list_kiotviet_jobs(request):
    requested_by = request.query_params.get("requested_by")
    order_id = request.query_params.get("order_id")
    limit = request.query_params.get("limit")
    filters = {
        "q": request.query_params.get("q", ""),
        "action": request.query_params.get("action", ""),
        "status": request.query_params.get("status", ""),
        "channel": request.query_params.get("channel", ""),
        "date_from": request.query_params.get("date_from", ""),
        "date_to": request.query_params.get("date_to", ""),
        "requested_by": int(requested_by) if (requested_by or "").isdigit() else None,
        "order_id": int(order_id) if (order_id or "").isdigit() else None,
        "limit": int(limit) if (limit or "").isdigit() else None,
    }

    # Record-scope THẬT thay cho `filters["requested_by"] = request.user.id`: khối cũ ép
    # MỌI non-superuser chỉ thấy job DO CHÍNH MÌNH bấm, nên trưởng nhóm scope
    # department/all không xem được job đồng bộ của nhân viên để xử lý sự cố; đồng thời
    # nó GHI ĐÈ luôn filter `requested_by` người dùng chọn trên UI (audit 2026-07-26 T3).
    #
    # Dùng `order_record_scope_q` (hợp Q theo từng section) thay vì
    # `resolved_channel_codes(user, "orders")`: endpoint guard bằng `require_section(
    # *ORDER_SECTIONS)` nên scope phải là HỢP của các section đó — role chỉ được cấp
    # koc-orders/landing-orders sẽ rơi về mặc định OWN của section "orders" nếu chốt
    # cứng một section. Đây cũng đúng khuôn `list_order_history` ở trên: job là trạng
    # thái đồng bộ CỦA ĐƠN, nên ai nhìn thấy đơn thì nhìn thấy job của đơn đó.
    queryset = OrderRepository.list_kiotviet_jobs(
        filters,
        order_scope_q=order_record_scope_q(request.user, ORDER_WRITE_SCOPE_SECTIONS),
    )
    serializer = OrderKiotVietJobSerializer(queryset, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


# Payload danh mục CRM (sales_status, reasons, need_types...) — tập key động theo
# cấu hình, mô tả dạng object tự do thay vì khai báo từng key.
@extend_schema(
    responses={200: OpenApiResponse(description="Danh mục CRM cho form đơn (object tự do)")},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def get_crm_catalogs(request):
    return Response(build_crm_catalog_payload(), status=status.HTTP_200_OK)


@extend_schema(
    request=OrderCreateKshipDeliverySerializer,
    responses={
        200: inline_serializer(
            "CreateKshipDeliveryResponse",
            fields={
                "detail": drf_serializers.CharField(),
                "created_count": drf_serializers.IntegerField(),
            },
        ),
        400: OpenApiResponse(description="Đơn thiếu hóa đơn/kênh KiotViet hoặc cấu hình KShip"),
        403: OpenApiResponse(description="Không có quyền thao tác một số đơn"),
        404: OpenApiResponse(description="Không tìm thấy đơn hàng hợp lệ"),
    },
)
# Tạo vận đơn KShip GHI shipping_code/trạng thái lên đơn có sẵn → :edit.
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_EDIT_CODES)])
def create_kship_delivery(request):
    """Create KShip delivery for selected orders."""
    serializer = OrderCreateKshipDeliverySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    ids = serializer.validated_data["ids"]
    provider_code = serializer.validated_data["provider_code"]
    service_code = serializer.validated_data["service_code"]
    notes = serializer.validated_data.get("notes", {})
    shipper_notes = serializer.validated_data.get("shipper_notes", {})
    service_extra = serializer.validated_data.get("service_extra", {})
    sender_address_id = serializer.validated_data.get("sender_address_id")
    if sender_address_id is not None and not KshipSenderAddress.objects.filter(id=sender_address_id).exists():
        return Response({"detail": "Địa chỉ gửi không tồn tại."}, status=status.HTTP_400_BAD_REQUEST)

    # Record-scope: tạo vận đơn là hành vi GHI + gọi ra ngoài (KShip), phải mirror đúng
    # read-scope — id ngoài phạm vi bị loại nên không tạo job KiotViet cho đơn người khác.
    orders = list(
        scoped_orders_queryset(
            request.user,
            ORDER_WRITE_SCOPE_SECTIONS,
            select_related=("channel", "kiotviet_channel"),
        ).filter(id__in=ids)
    )
    if not orders:
        return Response({"detail": "Không tìm thấy đơn hàng hợp lệ."}, status=status.HTTP_404_NOT_FOUND)

    forbidden_channels = sorted(
        {order.channel.code for order in orders if not OrderService.can_edit_channel(request.user, order.channel.code)}
    )
    if forbidden_channels:
        return Response(
            {"detail": "Bạn không có quyền thao tác một số đơn.", "channels": forbidden_channels},
            status=status.HTTP_403_FORBIDDEN,
        )

    missing_kiotviet_channel = any(order.kiotviet_channel_id is None for order in orders)
    if missing_kiotviet_channel:
        return Response({"detail": "Đơn hàng chưa được gán kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)

    missing_invoice_code = any(not order.invoice_code for order in orders)
    if missing_invoice_code:
        return Response(
            {"detail": "Đơn hàng chưa có mã hóa đơn. Vui lòng đẩy đơn lên KiotViet trước khi tạo mã vận đơn."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # KShip không có khoá chống trùng (payload["UUID"] luôn rỗng) nên gọi lại trên đơn
    # đã có mã là sinh vận đơn THẬT thứ hai bên hãng vận chuyển: mất phí thật, còn mã
    # cũ mồ côi vì Order chỉ giữ được một shipping_code. Chặn CẢ LÔ chứ không lọc bớt:
    # tạo một phần rồi báo lỗi khiến người dùng không biết đơn nào đã đi.
    orders_with_shipping_code = [order.id for order in orders if str(order.shipping_code or "").strip()]
    if orders_with_shipping_code:
        return Response(
            {
                "detail": "Đơn đã có mã vận đơn. Tạo lại sẽ sinh vận đơn thứ hai trên hãng vận chuyển.",
                "orders_with_shipping_code": orders_with_shipping_code,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    kiotviet_channel_ids = {order.kiotviet_channel_id for order in orders}
    if len(kiotviet_channel_ids) > 1:
        return Response({"detail": "Vui lòng chọn các đơn thuộc cùng một kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)

    kiotviet_channel = orders[0].kiotviet_channel
    if kiotviet_channel is None:
        return Response({"detail": "Không tìm thấy kênh KiotViet của đơn hàng."}, status=status.HTTP_400_BAD_REQUEST)

    if not str(kiotviet_channel.username or "").strip() or not str(kiotviet_channel.password or "").strip():
        return Response(
            {"detail": "Kênh KiotViet chưa cấu hình username/password cho luồng KShip."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    order_ids = [order.id for order in orders]
    # Giãn cách 1s giữa các job để tránh rate-limit phía KShip.
    result = OrderKiotVietJobService.enqueue(
        order_ids,
        OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY,
        request.user,
        event_source="orders.views.create_kship_delivery_queued",
        stagger_seconds=1,
        extra_fields_for=lambda order_id: {
            "kship_provider_code": provider_code,
            "kship_service_code": service_code,
            "kship_note": notes.get(str(order_id), ""),
            "kship_shipper_note": shipper_notes.get(str(order_id), ""),
            "kship_service_extra": service_extra,
            "kship_sender_address_id": sender_address_id,
        },
    )

    return Response(
        {
            "detail": f"Đã thêm {result.created_count} đơn vào hàng đợi tạo mã vận đơn KShip.",
            "created_count": result.created_count,
        },
        status=status.HTTP_200_OK,
    )
