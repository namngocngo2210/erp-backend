from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from apps.common.throttling import ClientIpMixin, ThrottleGuardMixin

from apps.warranty.models import WarrantyRegistration
from apps.warranty.serializers import WarrantyRegistrationSerializer


class WarrantyRegisterThrottle(ThrottleGuardMixin, ClientIpMixin, SimpleRateThrottle):
    """Throttle theo IP cho form đăng ký bảo hành public (chống spam/đầu độc dữ liệu).

    Endpoint không yêu cầu auth nên dùng IP làm định danh (ClientIpMixin — IP
    client thật sau Cloudflare+nginx). Rate đặt ở
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['warranty_register'].
    """

    scope = "warranty_register"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


# Form đăng ký bảo hành PUBLIC (không auth, throttle theo IP) — trả về bản ghi đã tạo (JSON).
@extend_schema(
    request=WarrantyRegistrationSerializer,
    responses={
        201: WarrantyRegistrationSerializer,
        400: OpenApiResponse(description="Dữ liệu đăng ký không hợp lệ"),
        429: OpenApiResponse(description="Vượt giới hạn tần suất gửi form"),
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([WarrantyRegisterThrottle])
def create_warranty_registration(request):
    serializer = WarrantyRegistrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    # Idempotent: form public dễ bị double-submit (double click/mất mạng retry).
    # Cùng (source, invoice_code, phone) → không tạo trùng bản ghi, coi như đã
    # nhận. Endpoint là AllowAny nên KHÔNG được trả lại bản ghi đã lưu (full_name/
    # email/note của người gửi lần đầu) — bất kỳ ai biết phone+invoice_code cũng
    # gọi được endpoint này. Trả về đúng payload người gửi lần này đã validate,
    # chỉ gắn thêm id/created_at của bản ghi đã có để giữ tính idempotent.
    existing = WarrantyRegistration.objects.filter(
        source=data.get("source"),
        invoice_code=data.get("invoice_code"),
        phone=data.get("phone"),
    ).first()
    if existing is not None:
        echoed = dict(serializer.data)
        echoed["id"] = serializer.fields["id"].to_representation(existing.id)
        echoed["created_at"] = serializer.fields["created_at"].to_representation(
            existing.created_at
        )
        return Response(echoed, status=status.HTTP_201_CREATED)

    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)
