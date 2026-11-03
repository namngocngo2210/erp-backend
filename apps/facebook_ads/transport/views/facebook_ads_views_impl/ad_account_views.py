"""View liệt kê tài khoản quảng cáo Facebook (theo app hoặc toàn hệ thống)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_section

from apps.facebook_ads.models import FacebookAdAccount
from apps.facebook_ads.serializers import FacebookAdAccountSerializer
from apps.facebook_ads.services import list_ad_accounts

from ._view_helpers import _get_app


# FE: danh sách TKQC của 1 app.
@extend_schema(
    responses={
        200: FacebookAdAccountSerializer(many=True),
        404: OpenApiResponse(description="Không tìm thấy FB App"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("facebook-ads")])
def list_app_ad_accounts(request, app_id: int):
    app = _get_app(app_id)
    if app is None:
        return Response({"detail": "Không tìm thấy FB App."}, status=status.HTTP_404_NOT_FOUND)
    include_removed = request.query_params.get("include_removed", "").lower() in ("1", "true", "yes")
    qs = list_ad_accounts(app, include_removed=include_removed)
    return Response(FacebookAdAccountSerializer(qs, many=True).data)


# FE: mọi TKQC active across apps — dùng cho UI map kênh.
@extend_schema(responses={200: FacebookAdAccountSerializer(many=True)})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("facebook-ads")])
def list_all_ad_accounts(request):
    """Return every active FB ad account across apps — used by Channel mapping UI."""
    include_removed = request.query_params.get("include_removed", "").lower() in ("1", "true", "yes")
    qs = FacebookAdAccount.objects.filter(app__deleted_at__isnull=True).select_related("app")
    if not include_removed:
        qs = qs.filter(status=FacebookAdAccount.STATUS_ACTIVE)
    qs = qs.order_by("app__name", "name")
    return Response(FacebookAdAccountSerializer(qs, many=True).data)
