"""View cấu hình Facebook App (tạo / liệt kê / sửa / xoá mềm)."""

from django.db import IntegrityError
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_method_permission

from apps.facebook_ads.models import FacebookApp
from apps.facebook_ads.serializers import (
    FacebookAppPartialUpdateSerializer,
    FacebookAppSerializer,
    FacebookAppUpsertSerializer,
)

from ._view_helpers import _get_app


# FE: trang cấu hình FB Ads — GET liệt kê app, POST tạo app mới.
@extend_schema(
    request=FacebookAppUpsertSerializer,
    responses={
        200: FacebookAppSerializer(many=True),
        201: FacebookAppSerializer,
        400: OpenApiResponse(description="App ID đã tồn tại / payload không hợp lệ"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="facebook-ads:view", POST="facebook-ads:edit")])
def list_create_apps(request):
    if request.method == "POST":
        serializer = FacebookAppUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            app = FacebookApp.objects.create(**serializer.validated_data)
        except IntegrityError:
            return Response({"detail": "App ID đã tồn tại."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(FacebookAppSerializer(app).data, status=status.HTTP_201_CREATED)

    qs = FacebookApp.objects.filter(deleted_at__isnull=True).order_by("-created_at")
    return Response(FacebookAppSerializer(qs, many=True).data)


# FE: sửa (PATCH) / xoá mềm (DELETE) một FB App.
@extend_schema(
    request=FacebookAppPartialUpdateSerializer,
    responses={
        200: FacebookAppSerializer,
        204: OpenApiResponse(description="Đã xoá mềm FB App"),
        400: OpenApiResponse(description="App ID đã tồn tại / payload không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy FB App"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="facebook-ads:edit", DELETE="facebook-ads:edit")])
def update_delete_app(request, app_id: int):
    app = _get_app(app_id)
    if app is None:
        return Response({"detail": "Không tìm thấy FB App."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        from django.utils import timezone

        app.deleted_at = timezone.now()
        app.save(update_fields=["deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = FacebookAppPartialUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    for field, value in serializer.validated_data.items():
        setattr(app, field, value)
    try:
        app.save()
    except IntegrityError:
        return Response({"detail": "App ID đã tồn tại."}, status=status.HTTP_400_BAD_REQUEST)
    return Response(FacebookAppSerializer(app).data)
