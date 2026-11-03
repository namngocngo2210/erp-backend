"""View liệt kê tài khoản quảng cáo Google Ads (theo config hoặc toàn hệ thống)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_section

from apps.google_ads.models import GoogleAdsAccount
from apps.google_ads.serializers import GoogleAdsAccountSerializer

from ._view_helpers import SECTION, _get_config


# FE: danh sách tài khoản của 1 config.
@extend_schema(
    responses={
        200: GoogleAdsAccountSerializer(many=True),
        404: OpenApiResponse(description="Không tìm thấy cấu hình Google Ads"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def list_config_accounts(request, config_id: int):
    config = _get_config(config_id)
    if config is None:
        return Response({"detail": "Không tìm thấy cấu hình Google Ads."}, status=status.HTTP_404_NOT_FOUND)
    qs = config.accounts.all().order_by("name")
    return Response(GoogleAdsAccountSerializer(qs, many=True).data)


# FE: mọi tài khoản Google Ads active across configs.
@extend_schema(responses={200: GoogleAdsAccountSerializer(many=True)})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def list_all_accounts(request):
    qs = (
        GoogleAdsAccount.objects.filter(config__deleted_at__isnull=True)
        .select_related("config")
        .order_by("name")
    )
    return Response(GoogleAdsAccountSerializer(qs, many=True).data)
