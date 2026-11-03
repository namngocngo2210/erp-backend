from __future__ import annotations

from collections import defaultdict

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.department_org_service import (
    DepartmentOrgService,
    OrgValidationError,
)
from apps.hr.models import DepartmentHead
from apps.hr.repositories import TeamRepository
from apps.hr.transport.serializers.department_org_serializers import (
    DepartmentOrgItemSerializer,
    HeadSetSerializer,
    TeamCreateSerializer,
    TeamSerializer,
    TeamUpdateSerializer,
)
from apps.hr.transport.views._helpers import forbidden as _forbidden
from apps.users.models import Department
from apps.users.rbac import resolve_effective_permissions


def _is_dept_admin(request) -> bool:
    """Cùng luật với trang quản lý phòng ban ở Quản trị (chỉ admin/superuser)."""
    return resolve_effective_permissions(request.user).is_superuser


# GET: tổ chức toàn bộ phòng ban {items:[{department_id, head, teams}]}.
@extend_schema(
    responses=inline_serializer(
        "HrDepartmentOrgList",
        fields={"items": DepartmentOrgItemSerializer(many=True)},
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def department_org(request):
    if not _is_dept_admin(request):
        return _forbidden()
    heads = {
        h.department_id: h.employee
        # Head đã nghỉ việc không hiện là "Trưởng phòng" (audit 2026-07-15b).
        for h in DepartmentHead.objects.select_related("employee").filter(
            employee__deleted_at__isnull=True
        )
    }
    teams_by_dept: dict[int, list] = defaultdict(list)
    for team in (
        TeamRepository.active()
        .select_related("lead")
        .prefetch_related("members__employee")
        .order_by("name")
    ):
        teams_by_dept[team.department_id].append(team)
    items = [
        {
            "department_id": dept_id,
            "head": heads.get(dept_id),
            "teams": teams_by_dept.get(dept_id, []),
        }
        for dept_id in Department.objects.values_list("id", flat=True)
    ]
    return Response({"items": DepartmentOrgItemSerializer(items, many=True).data})


# PUT: gán/gỡ trưởng phòng ({employee_id: number|null}) → 204.
@extend_schema(request=HeadSetSerializer, responses={204: None})
@api_view(["PUT"])
@permission_classes([IsAuthenticated])
@never_cache
def department_head_set(request, department_id: int):
    if not _is_dept_admin(request):
        return _forbidden()
    if not Department.objects.filter(id=department_id).exists():
        return Response({"detail": "Không tìm thấy phòng ban."}, status=404)
    ser = HeadSetSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        DepartmentOrgService.set_head(department_id, ser.validated_data["employee_id"])
    except OrgValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(status=status.HTTP_204_NO_CONTENT)


# POST: tạo team trong phòng ban → 201 (team đã tạo).
@extend_schema(request=TeamCreateSerializer, responses={201: TeamSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def department_teams(request, department_id: int):
    if not _is_dept_admin(request):
        return _forbidden()
    if not Department.objects.filter(id=department_id).exists():
        return Response({"detail": "Không tìm thấy phòng ban."}, status=404)
    ser = TeamCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        team = DepartmentOrgService.create_team(department_id, **ser.validated_data)
    except OrgValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        TeamSerializer(_reload_team(team.id)).data, status=status.HTTP_201_CREATED
    )


# PATCH: sửa team; DELETE: xoá mềm team (204).
@extend_schema(methods=["PATCH"], request=TeamUpdateSerializer, responses={200: TeamSerializer})
@extend_schema(methods=["DELETE"], responses={204: None})
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@never_cache
def team_detail(request, team_id: int):
    if not _is_dept_admin(request):
        return _forbidden()
    if request.method == "DELETE":
        try:
            DepartmentOrgService.delete_team(team_id)
        except OrgValidationError as exc:
            return Response({"detail": str(exc)}, status=404)
        return Response(status=status.HTTP_204_NO_CONTENT)
    ser = TeamUpdateSerializer(data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    try:
        team = DepartmentOrgService.update_team(team_id, ser.validated_data)
    except OrgValidationError as exc:
        code = 404 if "Không tìm thấy" in str(exc) else status.HTTP_400_BAD_REQUEST
        return Response({"detail": str(exc)}, status=code)
    return Response(TeamSerializer(_reload_team(team.id)).data)


def _reload_team(team_id: int):
    """Nạp lại team kèm lead + members để serialize không dính prefetch cũ."""
    return (
        TeamRepository.active()
        .select_related("lead")
        .prefetch_related("members__employee")
        .get(pk=team_id)
    )
