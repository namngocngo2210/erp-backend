"""View quản trị phòng ban."""

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

from apps.users.models import Department
from apps.users.permissions import require_method_permission, require_section
from apps.users.serializers import (
    DepartmentCreateUpdateSerializer,
    DepartmentSerializer,
)
from apps.users.services import DepartmentService

from ._view_helpers import _is_admin


@extend_schema_view(
    get=extend_schema(
        operation_id="admin_departments_list",
        responses={
            200: DepartmentSerializer(many=True),
            403: OpenApiResponse(description="Không có quyền quản lý phòng ban"),
        },
    ),
    post=extend_schema(
        operation_id="admin_departments_create",
        request=DepartmentCreateUpdateSerializer,
        responses={
            201: DepartmentSerializer,
            400: OpenApiResponse(description="Tên phòng ban đã tồn tại"),
            403: OpenApiResponse(description="Không có quyền quản lý phòng ban"),
        },
    ),
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="users:view", POST="users:create")])
def department_collection(request):
    if not _is_admin(request.user):
        return Response({"detail": "Ban khong co quyen quan ly phong ban."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        # prefetch cả channels lẫn staff_teams: `channel_ids` và `team_count` đều
        # duyệt related manager, không prefetch là 2 query mỗi phòng ban.
        departments = Department.objects.prefetch_related("channels", "staff_teams")
        return Response(DepartmentSerializer(departments, many=True).data)

    serializer = DepartmentCreateUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        dept = DepartmentService.create_department(serializer.validated_data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(DepartmentSerializer(dept).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    patch=extend_schema(
        operation_id="admin_departments_update",
        request=DepartmentCreateUpdateSerializer,
        responses={
            200: DepartmentSerializer,
            400: OpenApiResponse(description="Tên phòng ban đã tồn tại"),
            403: OpenApiResponse(description="Không có quyền quản lý phòng ban"),
        },
    ),
    delete=extend_schema(
        operation_id="admin_departments_delete",
        responses={
            204: OpenApiResponse(description="Đã xóa phòng ban"),
            403: OpenApiResponse(description="Không có quyền quản lý phòng ban"),
        },
    ),
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="users:edit", DELETE="users:delete")])
def department_detail(request, department_id: int):
    if not _is_admin(request.user):
        return Response({"detail": "Ban khong co quyen quan ly phong ban."}, status=status.HTTP_403_FORBIDDEN)

    dept = get_object_or_404(Department, id=department_id)

    if request.method == "DELETE":
        DepartmentService.delete_department(dept)
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = DepartmentCreateUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    try:
        dept = DepartmentService.update_department(dept, serializer.validated_data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(DepartmentSerializer(dept).data)
