"""Tổng hợp dữ liệu chấm công tháng cho export Excel.

Trả về cấu trúc dữ liệu thuần (list dict) — tầng infrastructure render openpyxl,
tầng transport chỉ wire. Xem docs/erp-hr-attendance-export.md.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.conf import settings

from apps.hr.domain.workday import WorkSchedule, evaluate_workday, schedule_from_values
from apps.hr.models import (
    AttendanceRecord,
    Employee,
    EmploymentStatus,
    LeaveRequest,
    LeaveStatus,
)

WEEKDAY_LABELS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]

NOTE_SUNDAY = "CN"
NOTE_LEAVE = "Phép"
NOTE_MISSING_PUNCH = "Quên chấm công"


@dataclass
class EmployeeMonthSummary:
    employee_code: str
    full_name: str
    department: str
    employee_id: int = 0
    total_credit: Decimal = Decimal("0")
    attended_days: int = 0
    total_late_minutes: int = 0
    total_early_minutes: int = 0
    missing_punch_days: int = 0
    leave_days: int = 0
    day_rows: list[dict] = field(default_factory=list)


class AttendanceExportError(ValueError):
    """Tháng không hợp lệ (định dạng YYYY-MM)."""


def _parse_month(month: str) -> tuple[int, int]:
    try:
        year_raw, _, month_raw = (month or "").partition("-")
        year, mo = int(year_raw), int(month_raw)
        if not 1 <= mo <= 12 or not 2000 <= year <= 2100:
            raise ValueError
        return year, mo
    except ValueError as exc:
        raise AttendanceExportError("Tháng không hợp lệ, cần định dạng YYYY-MM.") from exc


def _month_bounds(year: int, mo: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, mo)[1]
    return date(year, mo, 1), date(year, mo, last_day)


def month_bounds(month: str) -> tuple[date, date]:
    """Ngày đầu/cuối của tháng `YYYY-MM` (raise AttendanceExportError nếu sai định dạng).

    Public API cho các service khác (vd bảng lương cần biên tháng để chọn đúng bản ghi
    lương có hiệu lực) — không import hàm private xuyên module.
    """
    year, mo = _parse_month(month)
    return _month_bounds(year, mo)


def _active_employees(first: date, last: date) -> list[Employee]:
    """Nhân sự có làm việc trong tháng: chưa nghỉ trước đầu tháng, đã vào trước cuối tháng.

    Loại người xoá mềm và người Đã nghỉ không rõ ngày (data backfill thiếu
    resign_date) — họ không còn làm việc nên không thuộc bảng công/lương.
    """
    qs = (
        Employee.objects.select_related("department")
        .filter(deleted_at__isnull=True)
        .exclude(resign_date__lt=first)
        .exclude(resign_date__isnull=True, employment_status=EmploymentStatus.RESIGNED)
        .exclude(hire_date__gt=last)
        .order_by("department__name", "full_name")
    )
    return list(qs)


def _leave_dates_by_employee(
    employee_ids: list[int], first: date, last: date
) -> dict[int, set[date]]:
    """Map employee_id -> tập ngày nằm trong đơn nghỉ APPROVED giao với tháng."""
    leaves = LeaveRequest.objects.filter(
        employee_id__in=employee_ids,
        status=LeaveStatus.APPROVED,
        start_date__lte=last,
        end_date__gte=first,
    ).values_list("employee_id", "start_date", "end_date")

    result: dict[int, set[date]] = {}
    for employee_id, start, end in leaves:
        days = result.setdefault(employee_id, set())
        current = max(start, first)
        stop = min(end, last)
        while current <= stop:
            days.add(current)
            current = date.fromordinal(current.toordinal() + 1)
    return result


def schedule_from_settings() -> WorkSchedule:
    """Đọc giờ làm từ settings rồi đưa vào domain (domain không import django)."""
    return schedule_from_values(
        morning_start=getattr(settings, "HR_WORK_MORNING_START", ""),
        morning_end=getattr(settings, "HR_WORK_MORNING_END", ""),
        afternoon_start=getattr(settings, "HR_WORK_AFTERNOON_START", ""),
        afternoon_end=getattr(settings, "HR_WORK_AFTERNOON_END", ""),
    )


def build_month_export(month: str) -> list[EmployeeMonthSummary]:
    """Mỗi nhân sự một summary kèm đủ dòng mọi ngày trong tháng (1→cuối tháng)."""
    year, mo = _parse_month(month)
    first, last = _month_bounds(year, mo)
    schedule = schedule_from_settings()

    employees = _active_employees(first, last)
    employee_ids = [e.id for e in employees]

    records = AttendanceRecord.objects.filter(
        employee_id__in=employee_ids, work_date__gte=first, work_date__lte=last
    ).values_list("employee_id", "work_date", "check_in", "check_out")
    record_map = {(emp_id, day): (cin, cout) for emp_id, day, cin, cout in records}

    leave_map = _leave_dates_by_employee(employee_ids, first, last)

    summaries: list[EmployeeMonthSummary] = []
    for employee in employees:
        summary = EmployeeMonthSummary(
            employee_code=employee.employee_code,
            full_name=employee.full_name,
            department=employee.department.name if employee.department else "",
            employee_id=employee.id,
        )
        leave_days = leave_map.get(employee.id, set())

        for day_number in range(1, last.day + 1):
            work_date = date(year, mo, day_number)
            check_in, check_out = record_map.get((employee.id, work_date), (None, None))
            is_sunday = work_date.weekday() == 6
            has_any_punch = check_in is not None or check_out is not None

            result = evaluate_workday(check_in, check_out, schedule)

            notes = []
            if is_sunday:
                notes.append(NOTE_SUNDAY)
            if work_date in leave_days:
                notes.append(NOTE_LEAVE)
                summary.leave_days += 1
            # Có đúng 1 trong 2 mốc vào/ra → quên chấm công. Không chấm gì cả
            # thì là vắng/nghỉ, không tính quên.
            missing_punch = has_any_punch and (check_in is None or check_out is None)
            if missing_punch:
                notes.append(NOTE_MISSING_PUNCH)
                summary.missing_punch_days += 1

            summary.day_rows.append(
                {
                    "work_date": work_date,
                    "weekday": WEEKDAY_LABELS[work_date.weekday()],
                    "check_in": check_in,
                    "check_out": check_out,
                    "late_minutes": result.late_minutes if has_any_punch else 0,
                    "early_minutes": result.early_minutes if has_any_punch else 0,
                    "worked_minutes": result.worked_minutes,
                    "workday_credit": result.workday_credit,
                    "notes": ", ".join(notes),
                }
            )

            if check_in is not None and check_out is not None:
                summary.attended_days += 1
                summary.total_credit += result.workday_credit
                summary.total_late_minutes += result.late_minutes
                summary.total_early_minutes += result.early_minutes

        summaries.append(summary)
    return summaries
