"""Sinh dữ liệu bảng lương tháng theo format sheet T0X.2026 của BẢNG LƯƠNG 2026.xlsx.

Nguồn số liệu:
- Số công (D thử việc / E chính thức) + số bữa trưa (L): từ dữ liệu chấm công
  (``attendance_export_service``); tách D/E theo ``Employee.official_date``.
- Lương cơ bản (G): ``SalaryRecord`` CÓ HIỆU LỰC trong tháng xuất (bản ghi
  ``effective_from`` mới nhất ≤ ngày cuối tháng) — KHÔNG dùng ``is_current``, vì bảng
  lương tháng M thường được chốt đầu tháng M+1, khi đó một đợt tăng lương hiệu lực
  01/M+1 sẽ đổ nhầm lương mới vào tháng cũ (audit 2026-07-14).
- Công chuẩn/chuyên cần/phụ cấp/BHXH: ``EmployeeCompensation`` (chưa có bản ghi
  → default model).
- Cột HCNS điền tay, KHÔNG sinh: I/J chỉ prefill, K (lương KPI), O (phạt đi
  muộn), khối doanh thu P49+.

Xem docs/erp-hr-attendance-export.md phần Sprint 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from apps.hr.application.services.attendance_export_service import (
    build_month_export,
    month_bounds,
)
from apps.hr.models import Employee, EmployeeCompensation, EmploymentStatus, SalaryRecord


@dataclass
class PayrollRow:
    employee_code: str
    team: str
    full_name: str
    probation_workdays: Decimal  # D
    official_workdays: Decimal  # E
    standard_workdays: Decimal  # F
    base_salary: Decimal | None  # G
    attendance_bonus: Decimal  # tham số công thức H
    responsibility_allowance: Decimal  # I
    kpi_rate: Decimal | None  # J
    lunch_meals: int  # L
    lunch_per_meal: Decimal  # tham số công thức M
    other_allowance: Decimal  # N
    social_insurance: Decimal | None  # P
    bank_account_no: str  # X
    bank_name: str  # Y


def _split_workdays(summary_rows: list[dict], employee: Employee) -> tuple[Decimal, Decimal]:
    """Tách công thử việc (D) / chính thức (E) theo mốc ``official_date``."""
    probation = Decimal("0")
    official = Decimal("0")
    for row in summary_rows:
        credit = row["workday_credit"]
        if not credit:
            continue
        if employee.official_date is not None:
            if row["work_date"] < employee.official_date:
                probation += credit
            else:
                official += credit
        elif employee.employment_status == EmploymentStatus.PROBATION:
            probation += credit
        else:
            official += credit
    return probation, official


def _salaries_effective_in_month(employee_ids: list[int], last_day) -> dict[int, object]:
    """Lương cơ bản CÓ HIỆU LỰC trong tháng xuất, không phải lương hiện tại.

    HCNS chốt lương tháng M vào đầu tháng M+1. Nếu lấy `is_current` thì một đợt tăng
    lương hiệu lực 01/M+1 sẽ đổ lương MỚI vào bảng lương tháng M (audit 2026-07-14).
    Chọn bản ghi có `effective_from` mới nhất mà ≤ ngày cuối tháng xuất.
    """
    records = (
        SalaryRecord.objects.filter(
            employee_id__in=employee_ids, effective_from__lte=last_day
        )
        .order_by("employee_id", "-effective_from", "-id")
        .values_list("employee_id", "amount", "effective_from")
    )
    salaries: dict[int, object] = {}
    for employee_id, amount, _effective_from in records:
        # Query đã sort giảm dần theo effective_from → bản đầu tiên của mỗi NV là bản cần.
        salaries.setdefault(employee_id, amount)
    return salaries


def build_month_payroll(month: str) -> list[PayrollRow]:
    """Một dòng lương per nhân sự có làm việc trong tháng (thứ tự phòng ban/tên)."""
    summaries = build_month_export(month)
    employee_ids = [s.employee_id for s in summaries]
    _first_day, last_day = month_bounds(month)

    employees = {e.id: e for e in Employee.objects.filter(id__in=employee_ids)}
    compensations = {
        c.employee_id: c
        for c in EmployeeCompensation.objects.filter(employee_id__in=employee_ids)
    }
    salaries = _salaries_effective_in_month(employee_ids, last_day)

    rows: list[PayrollRow] = []
    for summary in summaries:
        employee = employees[summary.employee_id]
        comp = compensations.get(summary.employee_id) or EmployeeCompensation(employee=employee)
        probation, official = _split_workdays(summary.day_rows, employee)
        rows.append(
            PayrollRow(
                employee_code=summary.employee_code,
                team=summary.department,
                full_name=summary.full_name,
                probation_workdays=probation,
                official_workdays=official,
                standard_workdays=comp.standard_workdays,
                base_salary=salaries.get(summary.employee_id),
                attendance_bonus=comp.attendance_bonus,
                responsibility_allowance=comp.responsibility_allowance,
                kpi_rate=comp.kpi_rate,
                lunch_meals=summary.attended_days,
                lunch_per_meal=comp.lunch_per_meal,
                other_allowance=comp.other_allowance,
                social_insurance=comp.social_insurance,
                bank_account_no=employee.bank_account_no,
                bank_name=employee.bank_name,
            )
        )
    return rows
