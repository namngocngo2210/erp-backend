"""Render workbook Excel bảng chấm công tháng (2 sheet Chi tiết / Tổng hợp)."""

from __future__ import annotations

import io

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.common.excel_utils import append_clean_row
from apps.hr.application.services.attendance_export_service import EmployeeMonthSummary

_DETAIL_HEADERS = [
    "Mã NV",
    "Tên nhân viên",
    "Phòng ban",
    "Ngày",
    "Thứ",
    "Giờ vào",
    "Giờ ra",
    "Đi muộn (phút)",
    "Về sớm (phút)",
    "Giờ làm thực tế",
    "Công",
    "Ghi chú",
]

_SUMMARY_HEADERS = [
    "Mã NV",
    "Tên nhân viên",
    "Phòng ban",
    "Tổng công",
    "Ngày có chấm công",
    "Tổng đi muộn (phút)",
    "Tổng về sớm (phút)",
    "Số lần quên chấm công",
    "Số ngày phép",
]

_HEADER_FILL = PatternFill(start_color="DCEBFA", end_color="DCEBFA", fill_type="solid")
_SUNDAY_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")


def _fmt_hours(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}" if minutes else ""


def _write_header(ws, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"


def _autofit(ws, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def build_attendance_workbook(month: str, summaries: list[EmployeeMonthSummary]) -> bytes:
    wb = openpyxl.Workbook()

    detail = wb.active
    detail.title = "Chi tiết"
    _write_header(detail, _DETAIL_HEADERS)
    _autofit(detail, [10, 24, 16, 12, 6, 9, 9, 14, 14, 14, 8, 22])

    for summary in summaries:
        for row in summary.day_rows:
            has_punch = row["check_in"] is not None or row["check_out"] is not None
            # append_clean_row: tên NV / phòng ban do người dùng nhập, bắt đầu bằng "="
            # sẽ bị Excel evaluate khi mở file → neutralize (audit 2026-07-14).
            append_clean_row(
                detail,
                [
                    summary.employee_code,
                    summary.full_name,
                    summary.department,
                    row["work_date"].strftime("%d/%m/%Y"),
                    row["weekday"],
                    row["check_in"].strftime("%H:%M") if row["check_in"] else "",
                    row["check_out"].strftime("%H:%M") if row["check_out"] else "",
                    row["late_minutes"] if has_punch else "",
                    row["early_minutes"] if has_punch else "",
                    _fmt_hours(row["worked_minutes"]),
                    float(row["workday_credit"]) if row["worked_minutes"] else "",
                    row["notes"],
                ],
            )
            if row["weekday"] == "CN":
                for cell in detail[detail.max_row]:
                    cell.fill = _SUNDAY_FILL

    summary_ws = wb.create_sheet("Tổng hợp")
    _write_header(summary_ws, _SUMMARY_HEADERS)
    _autofit(summary_ws, [10, 24, 16, 10, 16, 18, 18, 20, 12])

    for summary in summaries:
        append_clean_row(
            summary_ws,
            [
                summary.employee_code,
                summary.full_name,
                summary.department,
                float(summary.total_credit),
                summary.attended_days,
                summary.total_late_minutes,
                summary.total_early_minutes,
                summary.missing_punch_days,
                summary.leave_days,
            ],
        )

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
