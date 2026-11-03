"""Endpoint public của portal tra cứu bảo hành (tra-cuu.delax.asia)."""

import secrets

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.common.throttling import ClientIpMixin
from apps.sysconfig import sysconfig
from apps.warranty_codes.application.services import activation_service, portal_orders_service
from apps.warranty_codes.models import PortalChannel, WarrantyActivation
from apps.warranty_codes.transport.portal_serializers import ActivateSerializer, TrackSerializer
from apps.warranty_codes.transport.portal_throttles import (
    PortalActivateThrottle,
    PortalMediaThrottle,
    PortalReadThrottle,
)


class _Ip(ClientIpMixin):
    """Chỉ để tái dùng logic đọc IP thật (CF → XFF → REMOTE_ADDR)."""


def _client_ip(request) -> str | None:
    return _Ip().get_ident(request) or None


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _warranty_payload(activation: WarrantyActivation) -> dict:
    code = activation.code
    product = code.product
    return {
        "serial": code.serial,
        "code": code.code,
        "product_sku": product.sku if product else "",
        "product_name": product.name if product else "",
        "activated_at": activation.activated_at,
        "expires_at": activation.expires_at,
        "days_left": (activation.expires_at - timezone.localdate()).days,
        "months": sysconfig.get("WARRANTY_PORTAL_MONTHS"),
    }


@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PortalActivateThrottle])
def activate(request):
    serializer = ActivateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    channel = serializer.validated_data["channel"]

    if channel == PortalChannel.ZALO_OA:
        expected = sysconfig.get("WARRANTY_PORTAL_ZALO_TOKEN")
        provided = request.META.get("HTTP_X_API_TOKEN", "")
        # encode() trước khi so: compare_digest với str non-ASCII (token gõ nhầm có
        # dấu tiếng Việt) raise TypeError → 500 thay vì 403. So bytes luôn an toàn.
        if not expected or not secrets.compare_digest(provided.encode(), expected.encode()):
            return Response({"detail": "Không có quyền truy cập."}, status=status.HTTP_403_FORBIDDEN)

    try:
        result = activation_service.activate(
            keyword=serializer.validated_data["keyword"],
            phone=serializer.validated_data["phone"],
            channel=channel,
            ip=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    if result.status == "not_found":
        return Response(
            {"detail": "Mã không hợp lệ. Sản phẩm có thể không phải hàng chính hãng.",
             "code": "not_found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    body = {"warranty": _warranty_payload(result.activation)}
    code = status.HTTP_201_CREATED if result.status == "activated" else status.HTTP_200_OK
    return Response(body, status=code)


# Trần số đơn trả về khi khách tra bằng SĐT. Khách lẻ mua nhiều nhất cũng chỉ
# vài đơn; con số này chặn trường hợp SĐT tổng đài/đại lý kéo về hàng trăm đơn
# làm nặng response của một endpoint public.
TRACK_LIST_LIMIT = 10


@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PortalReadThrottle])
def track(request):
    serializer = TrackSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    from apps.orders.models import Order

    # Từ bản sửa đổi 24/08 mỗi ô là một bộ lọc độc lập: nhập ô nào lọc theo ô đó,
    # nhập cả hai thì siết như cũ (AND). `validate()` của serializer đã bảo đảm
    # có ít nhất `shipping_code` hoặc `phone`, nên không bao giờ quét cả bảng.
    queryset = Order.objects.filter(deleted_at__isnull=True)
    if data["shipping_code"]:
        queryset = queryset.filter(shipping_code=data["shipping_code"])
    if data["order_code"]:
        queryset = queryset.filter(order_code=data["order_code"])
    if data["phone"]:
        queryset = queryset.filter(phone_normalized=data["phone"])

    # Lấy dư một bản ghi để biết danh sách có bị cắt hay không mà không tốn thêm
    # một COUNT trên bảng đơn.
    orders = list(
        queryset.prefetch_related("items").order_by("-created_at")[: TRACK_LIST_LIMIT + 1]
    )
    if not orders:
        # Cùng một thông báo cho MỌI trường hợp sai (mã sai, SĐT sai, đơn đã xoá
        # mềm) — không xác nhận mã vận đơn nào có thật.
        return Response(
            {"detail": "Không tìm thấy đơn hàng. Kiểm tra lại mã vận đơn hoặc số điện thoại.",
             "code": "not_found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if len(orders) == 1:
        order = orders[0]
        return Response({
            "order": portal_orders_service.order_summary(order),
            "journey": portal_orders_service.order_journey(order),
        })

    # Nhiều đơn → chỉ trả danh sách tóm tắt, KHÔNG kèm hành trình: mỗi hành trình
    # là vài query riêng (BestShipment + webhook event), dựng sẵn cho 10 đơn là
    # N+1 trong khi khách chỉ mở một đơn. Frontend gọi lại `track` kèm
    # `order_code` khi khách chọn.
    return Response({
        "orders": [portal_orders_service.order_summary(o) for o in orders[:TRACK_LIST_LIMIT]],
        "truncated": len(orders) > TRACK_LIST_LIMIT,
    })


@extend_schema(exclude=True)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PortalReadThrottle])
def product_page(request, code: str):
    from apps.warranty_codes.application.services import product_page_service

    result = product_page_service.load(
        keyword=code,
        ip=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    if not result.found:
        response = Response(
            {"detail": "Mã không hợp lệ. Sản phẩm có thể không phải hàng chính hãng.",
             "code": "not_found"},
            status=status.HTTP_404_NOT_FOUND,
        )
        # Nhánh 404 CŨNG có tác dụng phụ (product_page_service.load ghi log
        # ScanResult.NOT_FOUND) — cùng lý do phải no-store như nhánh 200 bên dưới.
        response["Cache-Control"] = "no-store"
        return response

    response = Response(product_page_service.build_payload(result.code))
    # Endpoint có tác dụng phụ (tăng counter, ghi nhật ký) — để proxy/trình duyệt
    # cache lại là mất lượt đếm và mất dấu vết chống tem giả.
    response["Cache-Control"] = "no-store"
    return response


# Ảnh sản phẩm: dùng PortalMediaThrottle (scope riêng warranty_portal_media),
# KHÔNG dùng PortalReadThrottle. Một trang kéo tới 8 ảnh + 1 lượt gọi payload =
# 9 request, bucket warranty_portal_read (120/giờ) sẽ cạn sau ~13 lượt xem. Và
# nếu view này không tự khai throttle nào, DRF rơi về DEFAULT_THROTTLE_CLASSES
# (bucket `anon` dùng CHUNG với mọi endpoint ẩn danh khác trong hệ thống) — tệ
# hơn cả hai phương án trên vì một IP xem vài chục trang sản phẩm sẽ kéo theo
# 429 lây sang các endpoint public không liên quan. Xem
# docs/product-scan-page-design.md §4.2 và PortalMediaThrottle.
@extend_schema(exclude=True)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PortalMediaThrottle])
def product_image(request, image_id: int):
    from apps.catalog.models import ProductProfileImage
    from apps.common.http_streaming import streaming_file_response
    from apps.orders.infrastructure import product_storage

    image = (
        ProductProfileImage.objects.select_related("profile")
        .filter(id=image_id, profile__is_published=True)
        .first()
    )
    if image is None:
        return Response({"detail": "Không tìm thấy ảnh."}, status=status.HTTP_404_NOT_FOUND)

    body, content_type, content_length = product_storage.get_object_stream(image.object_key)
    safe_type = content_type if content_type in _ALLOWED_IMAGE_TYPES else "application/octet-stream"
    # Stream thay vì đọc hết vào RAM (endpoint public, không cap dung lượng cho tới
    # khi Task 12 giới hạn upload) — obj["Body"] của boto3 vốn là file-like streaming.
    # Phải là async iterator: prod chạy UvicornWorker, iterator đồng bộ sẽ bị Django
    # gom trọn vào RAM trước khi gửi byte đầu (xem apps.common.http_streaming) —
    # đúng thứ mà endpoint không cần đăng nhập này không được phép cho phép.
    response = streaming_file_response(
        body, content_type=safe_type, content_length=content_length
    )
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'"
    return response
