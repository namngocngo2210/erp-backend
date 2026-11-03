import secrets

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.warranty.claim_service import (
    WarrantyClaimExists,
    WarrantyClaimService,
    WarrantyOrderAmbiguous,
    WarrantyOrderNotFound,
)
from apps.warranty.serializers import WarrantyClaimIngestSerializer, WarrantyClaimSerializer
from apps.sysconfig import sysconfig


def _extract_ingest_api_token(request) -> str:
    token = (request.headers.get("X-Api-Token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


# Ingest PUBLIC tạo phiếu bảo hành (token dùng chung ORDERS_INGEST_API_TOKEN, đồng bộ).
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def warranty_claim_ingest(request):
    expected_token = sysconfig.get("ORDERS_INGEST_API_TOKEN")
    if not expected_token:
        if getattr(settings, "IS_PRODUCTION", False):
            return Response(
                {"detail": "Warranty ingest is not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Non-production: lenient (cho dev chạy không cần token).
    else:
        provided = _extract_ingest_api_token(request)
        if not provided or not secrets.compare_digest(provided, expected_token):
            return Response(
                {"detail": "Invalid ingest API token."}, status=status.HTTP_401_UNAUTHORIZED
            )

    serializer = WarrantyClaimIngestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = dict(serializer.validated_data)
    shipping_code = data.pop("shipping_code")

    try:
        claim = WarrantyClaimService.ingest_claim_by_shipping_code(shipping_code, data)
    except WarrantyOrderNotFound:
        return Response(
            {"detail": "Không tìm thấy đơn theo mã vận đơn."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except WarrantyOrderAmbiguous:
        return Response(
            {"detail": "Nhiều đơn cùng mã vận đơn, không thể xác định."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except WarrantyClaimExists:
        return Response(
            {"detail": "Đơn hàng này đã có phiếu bảo hành."},
            status=status.HTTP_409_CONFLICT,
        )

    return Response(WarrantyClaimSerializer(claim).data, status=status.HTTP_200_OK)
