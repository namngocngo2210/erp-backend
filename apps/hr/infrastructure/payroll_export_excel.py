"""Render workbook bảng lương tháng — bám sát layout sheet T0X.2026 hiện dùng.

Cột A→AA giữ nguyên thứ tự/nhãn; công thức Excel sống trong file (H chuyên cần,
M ăn trưa, Q TỔNG, W chuyển ngoài) để HCNS sửa số là bảng tự tính lại. Cột
K (lương KPI), O (phạt), S→V (thanh toán) + khối doanh thu để trống chờ điền.
"""

from __future__ import annotations

import io

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.common.excel_utils import set_text_cell
from apps.hr.application.services.payroll_export_service import PayrollRow

_HEADERS = [
    "STT",
    "Team",
    "Tên nhân viên",
    "Số công thử việc",
    "Số công chính thức",
    "Công chuẩn",
    "Lương cơ bản",
    "Chuyên cần",
    "Phụ cấp trách nhiệm",
    "Mức doanh số / tỉ lệ KPI",
    "Lương hiệu quả (KPI)",
    "Ăn trưa\n(30k/ bữa)",
    "Phụ cấp ăn trưa",
    "Gửi xe/Phụ cấp thêm giờ",
    "Đi muộn/ Quên chấm công/ Vi phạm thời gian làm việc",
    "BHXH",
    "TỔNG",
    "Ghi chú",
    "Tạm ứng",
    "TT từ tk Cty",
    "TT từ tk HKD Hùng",
    "TT từ tk HKD Huyền",
    "Chuyển ngoài",
    "STK",
    "Ngân hàng",
    "Đã ok chưa",
    "Đã chuyển khoản",
]

_REVENUE_LABELS = [
    "Tổng doanh thu thuần",
    "TMĐT",
    "Sỉ/ Bán trực tiếp",
    "Resale",
    "Facebook",
    "Shopee",
    "tiktok",
    "google",
]

_HEADER_FILL = PatternFill(start_color="DCEBFA", end_color="DCEBFA", fill_type="solid")
_FIRST_DATA_ROW = 3


def _num(value) -> float | None:
    return float(value) if value is not None else None


def build_payroll_workbook(month: str, rows: list[PayrollRow]) -> bytes:
    year, _, mo = month.partition("-")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"T{mo}.{year}"

    ws.cell(row=1, column=4, value=f"BẢNG LƯƠNG THÁNG {mo}.{year}").font = Font(bold=True, size=14)

    ws.append([])  # đảm bảo header nằm ở dòng 2 như sheet gốc
    for col_index, header in enumerate(_HEADERS, start=1):
        cell = ws.cell(row=2, column=col_index, value=header)
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "D3"

    for index, row in enumerate(rows):
        r = _FIRST_DATA_ROW + index
        ws.cell(row=r, column=1, value=index + 1)
        # Cột chuỗi do người dùng nhập → neutralize formula (tên NV / phòng ban / ngân
        # hàng bắt đầu bằng "=" sẽ được Excel evaluate khi HCNS mở file).
        set_text_cell(ws, row=r, column=2, value=row.team)
        set_text_cell(ws, row=r, column=3, value=row.full_name)
        ws.cell(row=r, column=4, value=_num(row.probation_workdays) or None)
        ws.cell(row=r, column=5, value=_num(row.official_workdays) or None)
        ws.cell(row=r, column=6, value=_num(row.standard_workdays))
        ws.cell(row=r, column=7, value=_num(row.base_salary))
        if row.attendance_bonus:
            ws.cell(row=r, column=8, value=f"=IF(E{r}>=24,{int(row.attendance_bonus)},0)")
        if row.responsibility_allowance:
            ws.cell(row=r, column=9, value=_num(row.responsibility_allowance))
        if row.kpi_rate is not None:
            ws.cell(row=r, column=10, value=_num(row.kpi_rate))
        # K (lương KPI) để trống — HCNS điền công thức theo từng NV.
        if row.lunch_meals:
            ws.cell(row=r, column=12, value=row.lunch_meals)
            ws.cell(row=r, column=13, value=f"=L{r}*{int(row.lunch_per_meal)}")
        if row.other_allowance:
            ws.cell(row=r, column=14, value=_num(row.other_allowance))
        if row.social_insurance is not None:
            ws.cell(row=r, column=16, value=_num(row.social_insurance))
        ws.cell(
            row=r,
            column=17,
            value=(
                f"=G{r}/F{r}*D{r}*0.85+G{r}/F{r}*E{r}"
                f"+H{r}+I{r}+K{r}+M{r}+N{r}-O{r}-P{r}"
            ),
        )
        ws.cell(row=r, column=23, value=f"=Q{r}-S{r}-T{r}-U{r}-V{r}")
        set_text_cell(ws, row=r, column=24, value=row.bank_account_no or None)
        set_text_cell(ws, row=r, column=25, value=row.bank_name or None)

    total_row = _FIRST_DATA_ROW + len(rows)
    last_data_row = total_row - 1
    ws.cell(row=total_row, column=3, value="Tổng").font = Font(bold=True)
    if rows:
        for col_index in (11, 13, 14, 15, 16, 17, 19):
            letter = get_column_letter(col_index)
            ws.cell(
                row=total_row,
                column=col_index,
                value=f"=SUM({letter}{_FIRST_DATA_ROW}:{letter}{last_data_row})",
            ).font = Font(bold=True)

    # Khối doanh thu (neo công thức KPI của HCNS) — nhãn giữ nguyên, số để trống.
    revenue_start = total_row + 3
    for offset, label in enumerate(_REVENUE_LABELS):
        ws.cell(row=revenue_start + offset, column=15, value=label)
    ws.cell(row=revenue_start, column=16, value=f"=SUM(P{revenue_start + 2}:P{revenue_start + 7})")
    ws.cell(row=revenue_start + 1, column=16, value=f"=SUM(P{revenue_start + 4}:P{revenue_start + 7})")

    widths = [5, 10, 24, 10, 10, 9, 13, 11, 12, 12, 13, 9, 12, 12, 14, 11, 14, 12, 11, 12, 12, 12, 13, 18, 14, 9, 9]
    for col_index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_index)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
