"""Cơ chế lương per nhân sự + export bảng lương tháng.

Gate bằng section ``salaries`` (view/edit/export) — KHÔNG dùng ``employees:*``: role
HR nào được cấp section ``employees`` để quản lý hồ sơ cũng mặc nhiên có edit/export
của nó, tức là xem/sửa được lương + số tài khoản ngân hàng toàn công ty dù không có
``salaries:view`` (audit 2026-07-14). Mọi đường ra của dữ liệu lương đi chung 1 section.
Xem docs/erp-hr-attendance-export.md phần Sprint 2.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.attendance_export_service import AttendanceExportError
from apps.hr.application.services.payroll_export_service import build_month_payroll
from apps.hr.application.services.salary_service import SalaryService
from apps.hr.infrastructure.payroll_export_excel import build_payroll_workbook
from apps.hr.models import (
    Employee,
    EmployeeCompensation,
    EmploymentStatus,
    SalaryRecord,
)
from apps.hr.transport.serializers.compensation_serializers import (
    CompensationRowSerializer,
    CompensationUpdateSerializer,
)
from apps.hr.transport.views._helpers import can as _can
from apps.hr.transport.views._helpers import forbidden as _forbidden

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _row_payload(employee: Employee, comp: EmployeeCompensation, base_salary) -> dict:
    return {
        "employee_id": employee.id,
        "employee_code": employee.employee_code,
        "full_name": employee.full_name,
        "department": employee.department.name if employee.department else "",
        "base_salary": base_salary,
        "standard_workdays": comp.standard_workdays,
        "attendance_bonus": comp.attendance_bonus,
        "responsibility_allowance": comp.responsibility_allowance,
        "kpi_rate": comp.kpi_rate,
        "lunch_per_meal": comp.lunch_per_meal,
        "other_allowance": comp.other_allowance,
        "social_insurance": comp.social_insurance,
        "note": comp.note,
    }


# Danh sách cơ chế lương mọi nhân sự đang làm việc (chưa có bản ghi → default).
@extend_schema(
    responses=inline_serializer(
        "HrCompensationList",
        fields={"items": CompensationRowSerializer(many=True)},
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def compensation_list(request):
    if not _can(request, "salaries:view"):
        return _forbidden()

    # Đang làm việc = chưa xoá mềm, không mang trạng thái Đã nghỉ (data backfill
    # có người RESIGNED nhưng thiếu resign_date), và chưa qua ngày nghỉ.
    # localdate: `date.today()` chạy theo clock container (UTC) → 00:00-07:00 giờ VN
    # trả ngày hôm trước.
    employees = list(
        Employee.objects.select_related("department")
        .filter(deleted_at__isnull=True)
        .exclude(employment_status=EmploymentStatus.RESIGNED)
        .exclude(resign_date__lt=timezone.localdate())
        .order_by("department__name", "full_name")
    )
    comp_map = {
        c.employee_id: c
        for c in EmployeeCompensation.objects.filter(employee__in=employees)
    }
    salary_map = {
        s.employee_id: s.amount
        for s in SalaryRecord.objects.filter(employee__in=employees, is_current=True)
    }
    rows = [
        _row_payload(
            e,
            comp_map.get(e.id) or EmployeeCompensation(employee=e),
            salary_map.get(e.id),
        )
        for e in employees
    ]
    return Response({"items": CompensationRowSerializer(rows, many=True).data})


@extend_schema(
    request=CompensationUpdateSerializer,
    responses=CompensationRowSerializer,
)
@api_view(["PUT"])
@permission_classes([IsAuthenticated])
@never_cache
def compensation_update(request, employee_id: int):
    """Upsert cơ chế lương một nhân sự."""
    if not _can(request, "salaries:edit"):
        return _forbidden()

    employee = (
        Employee.objects.select_related("department").filter(id=employee_id).first()
    )
    if employee is None:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)

    ser = CompensationUpdateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = dict(ser.validated_data)
    new_base_salary = data.pop("base_salary", None)

    comp, _created = EmployeeCompensation.objects.update_or_create(
        employee=employee,
        defaults={**data, "updated_by": request.user},
    )

    current = SalaryRecord.objects.filter(employee=employee, is_current=True).first()
    base_salary = current.amount if current else None
    if new_base_salary is not None and new_base_salary != base_salary:
        # Hôm nay mới hơn mốc hiện tại → bản ghi mới (giữ lịch sử); cùng ngày
        # (hoặc bản hiện tại đề ngày tương lai) → sửa số bản hiện tại.
        effective_from = timezone.localdate()
        if current is not None and current.effective_from > effective_from:
            effective_from = current.effective_from
        record = SalaryService.set_salary(
            employee.id,
            amount=new_base_salary,
            effective_from=effective_from,
            reason="Cập nhật từ trang Cơ chế lương",
            created_by_id=request.user.id,
        )
        base_salary = record.amount

    return Response(CompensationRowSerializer(_row_payload(employee, comp, base_salary)).data)


# Trả file xlsx nhị phân qua HttpResponse → loại khỏi schema (convention chung).
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def payroll_export(request):
    """Sinh bảng lương tháng (format T0X.2026) — gate salaries:export."""
    if not _can(request, "salaries:export"):
        return _forbidden()

    month = (request.query_params.get("month") or "").strip()
    try:
        rows = build_month_payroll(month)
    except AttendanceExportError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    payload = build_payroll_workbook(month, rows)
    response = HttpResponse(payload, content_type=_XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="bang-luong-{month}.xlsx"'
    return response
