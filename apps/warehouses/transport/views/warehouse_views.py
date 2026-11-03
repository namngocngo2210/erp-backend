from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.users.permissions import require_method_permission
from apps.warehouses.repositories import WarehouseRepository
from apps.warehouses.transport.serializers.warehouse_serializers import WarehouseSerializer

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


# GET: danh sách kho phân trang {items,total,limit,offset}.
@extend_schema(
    methods=["GET"],
    responses={
        200: inline_serializer(
            "WarehouseListResponse",
            fields={
                "items": WarehouseSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                "limit": drf_serializers.IntegerField(),
                "offset": drf_serializers.IntegerField(),
            },
        ),
    },
)
# POST: tạo kho mới.
@extend_schema(
    methods=["POST"],
    request=WarehouseSerializer,
    responses={
        201: WarehouseSerializer,
        400: OpenApiResponse(description="Mã/tên kho trùng hoặc dữ liệu không hợp lệ"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="warehouses:view", POST="warehouses:create")])
@never_cache
def list_create_warehouses(request):
    if request.method == "GET":
        pagination, error_response = parse_required_pagination(
            request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
        )
        if error_response is not None:
            return error_response

        queryset = WarehouseRepository.active()
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(code__icontains=search))
        is_active = request.query_params.get("is_active")
        if is_active in ("true", "false"):
            queryset = queryset.filter(is_active=is_active == "true")

        total = queryset.count()
        items = WarehouseRepository.paginate(
            queryset.order_by("name", "id"),
            limit=pagination["limit"],
            offset=pagination["offset"],
        )
        return Response(
            {
                "items": WarehouseSerializer(items, many=True).data,
                "total": total,
                "limit": pagination["limit"],
                "offset": pagination["offset"],
            },
            status=status.HTTP_200_OK,
        )

    serializer = WarehouseSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        instance = serializer.save()
    except IntegrityError:
        return Response(
            {"detail": "Mã kho hoặc tên kho đã tồn tại."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response(WarehouseSerializer(instance).data, status=status.HTTP_201_CREATED)


# PATCH: cập nhật kho (partial).
@extend_schema(
    methods=["PATCH"],
    request=WarehouseSerializer,
    responses={
        200: WarehouseSerializer,
        400: OpenApiResponse(description="Mã/tên kho trùng hoặc dữ liệu không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy kho"),
    },
)
# DELETE: xoá mềm kho.
@extend_schema(
    methods=["DELETE"],
    responses={
        204: OpenApiResponse(description="Đã xoá kho"),
        404: OpenApiResponse(description="Không tìm thấy kho"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="warehouses:edit", DELETE="warehouses:delete")])
@never_cache
def update_delete_warehouse(request, warehouse_id: int):
    warehouse = WarehouseRepository.get_or_none(warehouse_id)
    if not warehouse:
        return Response({"detail": "Không tìm thấy kho."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        warehouse.deleted_at = timezone.now()
        warehouse.is_active = False
        warehouse.save(update_fields=["deleted_at", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = WarehouseSerializer(instance=warehouse, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    try:
        instance = serializer.save()
    except IntegrityError:
        return Response(
            {"detail": "Mã kho hoặc tên kho đã tồn tại."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response(WarehouseSerializer(instance).data, status=status.HTTP_200_OK)
