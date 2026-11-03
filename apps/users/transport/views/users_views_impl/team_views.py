"""View quản trị đội nhóm trong phòng ban.

Gác quyền bằng `users:view` (đọc) / `users:edit` (ghi) chứ KHÔNG bằng
`is_superuser` như API đội nhóm cũ bên `hr`: gác cứng superuser là một trong hai
lý do khiến tính năng cũ nằm im không ai dùng (xem docs/department-teams.md §1).
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_limit

from apps.users.models import Department, Team
from apps.users.permissions import require_method_permission
from apps.users.serializers import (
    DepartmentTeamCreateSerializer,
    DepartmentTeamSerializer,
    DepartmentTeamUpdateSerializer,
    TeamUserSerializer,
)
from apps.users.team_services import TeamService


def _team_payload(team_id: int) -> dict:
    """Đọc lại kèm prefetch để trả về đúng shape của bản ghi vừa ghi."""
    team = (
        Team.objects.select_related("lead").prefetch_related("members__user").get(id=team_id)
    )
    return DepartmentTeamSerializer(team).data


@extend_schema_view(
    get=extend_schema(
        operation_id="users_department_teams_list",
        responses={
            200: DepartmentTeamSerializer(many=True),
            403: OpenApiResponse(description="Không có quyền xem đội nhóm"),
            404: OpenApiResponse(description="Không tìm thấy phòng ban"),
        },
    ),
    post=extend_schema(
        operation_id="users_department_teams_create",
        request=DepartmentTeamCreateSerializer,
        responses={
            201: DepartmentTeamSerializer,
            400: OpenApiResponse(description="Trùng tên hoặc người được chọn ngoài phòng ban"),
            403: OpenApiResponse(description="Không có quyền tạo đội nhóm"),
        },
    ),
)
@api_view(["GET", "POST"])
@permission_classes(
    [IsAuthenticated, require_method_permission(GET="users:view", POST="users:edit")]
)
def department_teams(request, department_id: int):
    get_object_or_404(Department, id=department_id)

    if request.method == "GET":
        teams = TeamService.teams_for_department(department_id)
        return Response(DepartmentTeamSerializer(teams, many=True).data)

    serializer = DepartmentTeamCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        team = TeamService.create_team(department_id, serializer.validated_data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_team_payload(team.id), status=status.HTTP_201_CREATED)


@extend_schema_view(
    patch=extend_schema(
        operation_id="users_teams_update",
        request=DepartmentTeamUpdateSerializer,
        responses={
            200: DepartmentTeamSerializer,
            400: OpenApiResponse(description="Trùng tên hoặc người được chọn ngoài phòng ban"),
            403: OpenApiResponse(description="Không có quyền sửa đội nhóm"),
            404: OpenApiResponse(description="Không tìm thấy đội nhóm"),
        },
    ),
    delete=extend_schema(
        operation_id="users_teams_delete",
        responses={
            204: OpenApiResponse(description="Đã xoá đội nhóm"),
            403: OpenApiResponse(description="Không có quyền xoá đội nhóm"),
        },
    ),
)
@api_view(["PATCH", "DELETE"])
@permission_classes(
    [IsAuthenticated, require_method_permission(PATCH="users:edit", DELETE="users:edit")]
)
def team_detail(request, team_id: int):
    # `deleted_at__isnull=True`: nhóm đã xoá mềm phải là 404, không được sửa tiếp.
    team = get_object_or_404(Team, id=team_id, deleted_at__isnull=True)

    if request.method == "DELETE":
        TeamService.delete_team(team)
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = DepartmentTeamUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    try:
        TeamService.update_team(team, serializer.validated_data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_team_payload(team.id))


@extend_schema(
    operation_id="users_department_assignable_users",
    responses={
        200: TeamUserSerializer(many=True),
        403: OpenApiResponse(description="Không có quyền xem danh sách tài khoản"),
        404: OpenApiResponse(description="Không tìm thấy phòng ban"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_method_permission(GET="users:view")])
def department_assignable_users(request, department_id: int):
    """Tài khoản gán được vào đội nhóm của phòng ban — nguồn cho ô chọn thành viên."""
    get_object_or_404(Department, id=department_id)
    # Trần dòng (audit 2026-09-23 L6): một phòng ban vài chục người; FE không gửi
    # limit vẫn nhận đủ như trước, response giữ dạng list.
    limit, limit_error = parse_limit(request, default=500, max_limit=500)
    if limit_error is not None:
        return limit_error
    users = TeamService.assignable_users(department_id)[:limit]
    return Response(TeamUserSerializer(users, many=True).data)
