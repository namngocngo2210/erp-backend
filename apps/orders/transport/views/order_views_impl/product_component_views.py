"""2 endpoint khai báo thành phần combo — bám khuôn function-based view của
``product_catalog_views`` (cùng quyền, cùng ``never_cache``)."""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.product_component_service import (
    ProductComponentService,
)
from apps.users.permissions import ORDER_SECTIONS, require_method_permission

_ROW = inline_serializer(
    "ProductComponentRow",
    fields={
        "component_id": drf_serializers.IntegerField(),
        "sku": drf_serializers.CharField(),
        "name": drf_serializers.CharField(),
        "quantity": drf_serializers.IntegerField(),
    },
)


@extend_schema(
    request=inline_serializer(
        "ProductComponentsRequest",
        fields={
            "components": inline_serializer(
                "ProductComponentInput",
                many=True,
                fields={
                    "component_id": drf_serializers.IntegerField(),
                    "quantity": drf_serializers.IntegerField(),
                },
            ),
        },
    ),
    responses={
        # GET/PUT đều trả một MẢNG dòng thành phần, không phải một object đơn
        # — many=True để schema.yaml phát sinh đúng `type: array`.
        200: _ROW.__class__(many=True),
        400: OpenApiResponse(description="Dữ liệu thành phần không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy sản phẩm"),
    },
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_method_permission(GET=tuple(f"{x}:view" for x in ("products", *ORDER_SECTIONS)), PUT=tuple(f"{x}:edit" for x in ("products", *ORDER_SECTIONS)))])
@never_cache
def product_components(request, product_id: int):
    """Đọc / ghi đè danh sách thành phần của một SKU combo.

    PUT ghi đè TRỌN danh sách (không PATCH từng dòng): người dùng sửa cả bảng
    trong một dialog rồi bấm Lưu, ghi đè tránh trạng thái nửa vời.
    """
    if request.method == "GET":
        return Response(ProductComponentService.list_for(product_id), status=status.HTTP_200_OK)
    rows = request.data.get("components")
    if not isinstance(rows, list):
        return Response({"detail": "Thiếu danh sách components."}, status=status.HTTP_400_BAD_REQUEST)
    return Response(ProductComponentService.replace(product_id, rows), status=status.HTTP_200_OK)
