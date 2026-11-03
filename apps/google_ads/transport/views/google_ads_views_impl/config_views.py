"""View CRUD cấu hình Google Ads (MCC)."""

from django.db import IntegrityError
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_method_permission

from apps.google_ads.models import GoogleAdsConfig
from apps.google_ads.serializers import (
    GoogleAdsConfigPartialUpdateSerializer,
    GoogleAdsConfigSerializer,
    GoogleAdsConfigUpsertSerializer,
)

from ._view_helpers import SECTION, _get_config


# FE: trang cấu hình Google Ads — GET liệt kê config, POST tạo config mới.
@extend_schema(
    request=GoogleAdsConfigUpsertSerializer,
    responses={
        200: GoogleAdsConfigSerializer(many=True),
        201: GoogleAdsConfigSerializer,
        400: OpenApiResponse(description="Login Customer ID (MCC) đã tồn tại"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=f"{SECTION}:view", POST=f"{SECTION}:edit")])
def list_create_configs(request):
    if request.method == "POST":
        serializer = GoogleAdsConfigUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            config = GoogleAdsConfig.objects.create(**serializer.validated_data)
        except IntegrityError:
            return Response(
                {"detail": "Login Customer ID (MCC) đã tồn tại."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(GoogleAdsConfigSerializer(config).data, status=status.HTTP_201_CREATED)

    qs = GoogleAdsConfig.objects.filter(deleted_at__isnull=True).order_by("-created_at")
    return Response(GoogleAdsConfigSerializer(qs, many=True).data)


# FE: sửa (PATCH) / xoá mềm (DELETE) một config Google Ads.
@extend_schema(
    request=GoogleAdsConfigPartialUpdateSerializer,
    responses={
        200: GoogleAdsConfigSerializer,
        204: OpenApiResponse(description="Đã xoá mềm config"),
        400: OpenApiResponse(description="Login Customer ID (MCC) đã tồn tại"),
        404: OpenApiResponse(description="Không tìm thấy cấu hình Google Ads"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=f"{SECTION}:edit", DELETE=f"{SECTION}:edit")])
def update_delete_config(request, config_id: int):
    config = _get_config(config_id)
    if config is None:
        return Response({"detail": "Không tìm thấy cấu hình Google Ads."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        config.deleted_at = timezone.now()
        config.save(update_fields=["deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = GoogleAdsConfigPartialUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    for field, value in serializer.validated_data.items():
        setattr(config, field, value)
    try:
        config.save()
    except IntegrityError:
        return Response(
            {"detail": "Login Customer ID (MCC) đã tồn tại."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(GoogleAdsConfigSerializer(config).data)
