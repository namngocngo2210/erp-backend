"""Định nghĩa export của app callcenter — đăng ký vào engine ở CallcenterConfig.ready()."""

from __future__ import annotations

from datetime import date

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.common.dates import parse_iso_date
from apps.exports.application.registry import ExportDefinition, register_export
from apps.users.auth_core import user_has_any_permission

from . import quality_reports
from .quality_export_excel import (
    CALL_SHEET_HEADERS,
    CRITERION_SHEET_HEADERS,
    QUALITY_EXPORT_MAX_ROWS,
    call_row_values,
    criterion_row_values,
    summary_sheet_rows,
)

# Số dòng nạp mỗi lô. Nhỏ hơn của orders (2000) vì mỗi bản ghi ở đây kéo theo 2
# cột JSONB nặng (`criteria` + `red_flags`) chứ không chỉ cột thường.
_CHUNK = 500


def _parse_date(raw, field_label: str) -> date | None:
    try:
        return parse_iso_date(raw, strict=True)
    except ValueError as exc:
        raise ValidationError({"detail": f"{field_label} không hợp lệ."}) from exc


def _optional_int(raw, field_label: str) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"detail": f"{field_label} không hợp lệ."}) from exc


def _text(raw, field_label: str) -> str:
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        raise ValidationError({"detail": f"{field_label} không hợp lệ."})
    return raw.strip()


def _period(params: dict) -> quality_reports.DateRange:
    filters = params["filters"]
    return quality_reports.DateRange(
        start=date.fromisoformat(filters["start"]), end=date.fromisoformat(filters["end"])
    )


class CallQualityExport(ExportDefinition):
    """Xuất 3 sheet của trang Chất lượng cuộc gọi theo đúng bộ lọc đang áp."""

    kind = "call_quality"

    def validate(self, user, raw_params: dict) -> dict:
        filters_input = raw_params.get("filters")
        if filters_input is not None and not isinstance(filters_input, dict):
            raise ValidationError({"detail": "Bộ lọc không hợp lệ."})
        raw = dict(filters_input or {})

        # Khoảng ngày phải CHỐT ở đây thành ngày cụ thể, không để rỗng rồi cho
        # `write` tự lấy "30 ngày gần nhất": job chạy nền có thể khởi động sau nửa
        # đêm, khi đó file xuất ra lệch một ngày so với màn hình lúc bấm nút. Chốt
        # sẵn cũng làm params_hash ổn định, tức dedupe chống double-click mới đúng.
        period = quality_reports.default_range()
        start = _parse_date(raw.get("start"), "Ngày bắt đầu") or period.start
        end = _parse_date(raw.get("end"), "Ngày kết thúc") or period.end
        if start > end:
            start, end = end, start

        # Khoá trùng ĐÚNG tên query param của endpoint danh sách — frontend gửi
        # thẳng bộ lọc đang áp, không phải dịch sang bộ tên thứ hai (bộ tên thứ
        # hai là chỗ file Excel bắt đầu trôi khỏi màn hình).
        return {
            "user_id": user.id,
            "filters": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "sales_status": _text(raw.get("sales_status"), "Trạng thái sale"),
                "staff": _text(str(raw["staff"]) if raw.get("staff") else "", "Sale"),
                "sku": _text(raw.get("sku"), "SKU"),
                "criterion": _text(raw.get("criterion"), "Tiêu chí"),
                "min_score": _optional_int(raw.get("min_score"), "Điểm tối thiểu"),
                "max_score": _optional_int(raw.get("max_score"), "Điểm tối đa"),
                "has_red_flag": bool(raw.get("has_red_flag")),
            },
        }

    def check_permission(self, user, params: dict) -> None:
        if not user_has_any_permission(user, "call-quality:export"):
            raise PermissionDenied("Bạn không có quyền xuất Excel ở trang này.")

    def estimate(self, params: dict) -> int | None:
        matched = quality_reports.filter_calls(_period(params), params["filters"]).count()
        if matched > QUALITY_EXPORT_MAX_ROWS:
            raise ValidationError(
                {
                    "detail": (
                        f"Bộ lọc khớp {matched} cuộc gọi, vượt giới hạn "
                        f"{QUALITY_EXPORT_MAX_ROWS} cuộc mỗi lần xuất. Vui lòng thu hẹp "
                        "khoảng ngày hoặc bộ lọc rồi thử lại."
                    )
                }
            )
        return matched

    def file_name(self, params: dict) -> str:
        return f"chat-luong-cuoc-goi-{timezone.localtime(timezone.now()):%Y%m%d-%H%M%S}.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        period = _period(params)
        filters = params["filters"]
        pass_score = quality_reports.current_pass_score()

        # Quét queryset HAI lần (sheet 1 rồi sheet 2) thay vì giữ toàn bộ bản ghi
        # trong RAM để ghi một lượt: writer là streaming, sheet phải ghi tuần tự,
        # và 20.000 bản ghi kèm JSONB `criteria` giữ trong bộ nhớ worker là cái
        # giá cao hơn một lượt quét DB nữa.
        writer.set_sheet_title("Cuộc gọi")
        writer.write_header(CALL_SHEET_HEADERS)
        count = 0
        for score in self._rows(period, filters):
            writer.write_row(call_row_values(score, pass_score=pass_score))
            count += 1
            if count % 200 == 0:
                progress(count)
        progress(count)

        writer.start_sheet("Điểm tiêu chí")
        writer.write_header(CRITERION_SHEET_HEADERS)
        for score in self._rows(period, filters):
            for row in criterion_row_values(score):
                writer.write_row(row)
        progress(count)

        # Sheet tổng hợp đi qua CÙNG bộ lọc với sheet 1: xuất kèm lọc SKU mà ô
        # "số cuộc đã chấm" vẫn là con số của toàn kỳ thì file tự mâu thuẫn.
        writer.start_sheet("Tổng hợp")
        summary = quality_reports.build_summary(period, filters)
        status_breakdown = quality_reports.build_status_breakdown(period, filters)
        for row in summary_sheet_rows(summary, status_breakdown):
            writer.write_row(row)
        progress(count)

    def _rows(self, period, filters):
        return (
            quality_reports.filter_calls(period, filters)
            .select_related(
                "call_record",
                "call_record__order",
                "call_record__order__staff",
                "call_record__order__assessment",
            )
            .prefetch_related("call_record__order__items")
            .order_by("-call_date", "-id")
            .iterator(chunk_size=_CHUNK)
        )


register_export(CallQualityExport())
