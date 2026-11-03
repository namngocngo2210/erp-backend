"""View RBAC: catalog permission và CRUD role."""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import Permission, Role
from apps.users.permissions import require_method_permission, require_section
from apps.users.rbac_catalog import ORDER_FIELD_GROUPS, ORDER_FIELD_LABELS
from apps.users.serializers import (
    PermissionSerializer,
    RoleSerializer,
    RoleWriteSerializer,
)
from apps.users.services import RoleService

from ._view_helpers import _is_admin


@extend_schema(
    operation_id="admin_permissions_catalog",
    responses={
        200: PermissionSerializer(many=True),
        403: OpenApiResponse(description="Không có quyền"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("users")])
def permissions_catalog(request):
    if not _is_admin(request.user):
        return Response({"detail": "Khong co quyen."}, status=status.HTTP_403_FORBIDDEN)
    perms = Permission.objects.all().order_by("section", "action")
    return Response(PermissionSerializer(perms, many=True).data)


@extend_schema(
    operation_id="admin_order_field_catalog",
    responses={
        200: OpenApiResponse(description="Nhóm field đơn hàng dùng để cấu hình quyền sửa field"),
        403: OpenApiResponse(description="Không có quyền"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("users")])
def order_field_catalog(request):
    """Nhóm field đơn + nhãn tiếng Việt cho tab cấu hình quyền sửa field.

    Nguồn sự thật ở backend: frontend hard-code danh sách thì hai bên lệch nhau chỉ
    là chuyện thời gian, và lệch ở đây nghĩa là admin tick một field không tồn tại.
    """
    if not _is_admin(request.user):
        return Response({"detail": "Khong co quyen."}, status=status.HTTP_403_FORBIDDEN)
    return Response(
        {
            "groups": [
                {
                    "code": code,
                    "label": label,
                    "fields": [
                        {"name": f, "label": ORDER_FIELD_LABELS[f]} for f in fields
                    ],
                }
                for code, label, fields in ORDER_FIELD_GROUPS
            ]
        }
    )


@extend_schema_view(
    get=extend_schema(
        operation_id="admin_roles_list",
        responses={
            200: RoleSerializer(many=True),
            403: OpenApiResponse(description="Không có quyền"),
        },
    ),
    post=extend_schema(
        operation_id="admin_roles_create",
        request=RoleWriteSerializer,
        responses={
            201: RoleSerializer,
            400: OpenApiResponse(description="Thiếu tên role hoặc tên role đã tồn tại"),
            403: OpenApiResponse(description="Không có quyền"),
        },
    ),
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="users:view", POST="users:create")])
def role_collection(request):
    if not _is_admin(request.user):
        return Response({"detail": "Khong co quyen."}, status=status.HTTP_403_FORBIDDEN)
    if request.method == "GET":
        return Response(RoleSerializer(RoleService.list_roles(), many=True).data)

    serializer = RoleWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if not data.get("name"):
        return Response({"detail": "Thiếu tên role."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        role = RoleService.create_role(data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(RoleSerializer(role).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    patch=extend_schema(
        operation_id="admin_roles_update",
        request=RoleWriteSerializer,
        responses={
            200: RoleSerializer,
            400: OpenApiResponse(description="Tên role đã tồn tại hoặc role hệ thống không thể sửa"),
            403: OpenApiResponse(description="Không có quyền"),
        },
    ),
    delete=extend_schema(
        operation_id="admin_roles_delete",
        responses={
            204: OpenApiResponse(description="Đã xóa role"),
            400: OpenApiResponse(description="Role hệ thống không thể xóa"),
            403: OpenApiResponse(description="Không có quyền"),
        },
    ),
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="users:edit", DELETE="users:delete")])
def role_detail(request, role_id: int):
    if not _is_admin(request.user):
        return Response({"detail": "Khong co quyen."}, status=status.HTTP_403_FORBIDDEN)
    role = get_object_or_404(Role, id=role_id)
    if role.is_system:
        return Response(
            {"detail": "Không thể sửa/xóa role hệ thống."}, status=status.HTTP_400_BAD_REQUEST
        )

    if request.method == "DELETE":
        RoleService.delete_role(role)
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = RoleWriteSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    try:
        role = RoleService.update_role(role, serializer.validated_data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(RoleSerializer(role).data)
