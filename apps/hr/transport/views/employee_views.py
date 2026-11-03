from __future__ import annotations

from django.db import IntegrityError
from django.db.models import Q
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.hr.application.services.employee_service import (
    DuplicateEmployeeCodeError,
    EmployeeService,
)
from apps.hr.domain.org_chart import OrgChartCycleError
from apps.hr.models import EmploymentStatus
from apps.hr.repositories import EmployeeRepository
from apps.hr.transport.serializers.employee_serializers import (
    EmployeeCreateSerializer,
    EmployeeSerializer,
    EmployeeUpdateSerializer,
)
from apps.hr.transport.views._helpers import can as _can
from apps.hr.transport.views._helpers import forbidden as _forbidden

_MAX_LIMIT = 200


# GET: danh sách nhân sự phân trang {items,total,limit,offset}; POST: tạo mới (201).
@extend_schema(
    methods=["GET"],
    responses=inline_serializer(
        "HrEmployeeList",
        fields={
            "items": EmployeeSerializer(many=True),
            "total": drf_serializers.IntegerField(),
            "limit": drf_serializers.IntegerField(),
            "offset": drf_serializers.IntegerField(),
        },
    ),
)
@extend_schema(
    methods=["POST"],
    request=EmployeeCreateSerializer,
    responses={201: EmployeeSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def list_create_employees(request):
    if request.method == "GET":
        if not _can(request, "employees:view"):
            return _forbidden()
        pagination, err = parse_required_pagination(
            request, max_limit=_MAX_LIMIT, default_limit=50
        )
        if err is not None:
            return err
        qs = EmployeeRepository.list_with_relations()
        dept = request.query_params.get("department")
        if dept:
            qs = qs.filter(department_id=dept)
        status_f = request.query_params.get("status")
        if status_f:
            qs = qs.filter(employment_status=status_f)
        # Lọc theo "đã dừng làm việc hay chưa": working = mọi trạng thái trừ
        # resigned (thử việc/chính thức/tạm nghỉ), resigned = đã nghỉ việc.
        state = request.query_params.get("state")
        if state == "working":
            qs = qs.exclude(employment_status=EmploymentStatus.RESIGNED)
        elif state == "resigned":
            qs = qs.filter(employment_status=EmploymentStatus.RESIGNED)
        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(full_name__icontains=q)
                | Q(employee_code__icontains=q)
                | Q(phone__icontains=q)
            )
        total = qs.count()
        items = EmployeeRepository.paginate(
            qs.order_by("employee_code"),
            limit=pagination["limit"],
            offset=pagination["offset"],
        )
        return Response(
            {
                "items": EmployeeSerializer(items, many=True, context={'request': request}).data,
                "total": total,
                "limit": pagination["limit"],
                "offset": pagination["offset"],
            }
        )

    if not _can(request, "employees:create"):
        return _forbidden()
    ser = EmployeeCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        emp = EmployeeService.create(**ser.validated_data)
    except DuplicateEmployeeCodeError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except IntegrityError:
        return Response(
            {"detail": "Dữ liệu bị trùng (tên đăng nhập hoặc mã nhân viên)."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(EmployeeSerializer(emp, context={'request': request}).data, status=status.HTTP_201_CREATED)


# GET: chi tiết nhân sự; PATCH: cập nhật một phần; DELETE: xoá (204).
@extend_schema(
    methods=["GET"],
    responses={200: EmployeeSerializer},
)
@extend_schema(
    methods=["PATCH"],
    request=EmployeeUpdateSerializer,
    responses={200: EmployeeSerializer},
)
@extend_schema(
    methods=["DELETE"],
    responses={204: None},
)
@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_detail(request, employee_id: int):
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    if request.method == "GET":
        if not _can(request, "employees:view"):
            return _forbidden()
        return Response(EmployeeSerializer(emp, context={'request': request}).data)
    if request.method == "DELETE":
        if not _can(request, "employees:delete"):
            return _forbidden()
        EmployeeService.delete(employee_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
    if not _can(request, "employees:edit"):
        return _forbidden()
    # Validate + normalize (accepts both ``department``/``department_id`` shapes)
    # so relation changes are never silently dropped and the sync event fires.
    ser = EmployeeUpdateSerializer(data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    try:
        emp = EmployeeService.update(employee_id, ser.validated_data)
    except (OrgChartCycleError, DuplicateEmployeeCodeError) as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(EmployeeSerializer(emp, context={'request': request}).data)


# Cho nhân viên nghỉ việc — không có body, trả về nhân sự đã cập nhật.
@extend_schema(
    request=None,
    responses={200: EmployeeSerializer},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_resign(request, employee_id: int):
    """Cho nhân viên dừng làm việc: status=resigned + vô hiệu hoá tài khoản."""
    if not _can(request, "employees:delete"):
        return _forbidden()
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    emp = EmployeeService.resign(employee_id)
    return Response(EmployeeSerializer(emp, context={'request': request}).data)


# Sơ đồ tổ chức: nhân sự + danh sách cấp dưới trực tiếp.
@extend_schema(
    responses=inline_serializer(
        "HrEmployeeOrgChart",
        fields={
            "employee": EmployeeSerializer(),
            "reports": EmployeeSerializer(many=True),
        },
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_org_chart(request, employee_id: int):
    if not _can(request, "employees:view"):
        return _forbidden()
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    reports = (
        EmployeeRepository.active()
        .filter(manager_id=emp.id)
        .prefetch_related("accounts__user")
    )
    return Response(
        {
            "employee": EmployeeSerializer(emp, context={'request': request}).data,
            "reports": EmployeeSerializer(reports, many=True, context={'request': request}).data,
        }
    )
