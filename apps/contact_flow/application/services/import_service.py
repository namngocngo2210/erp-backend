"""Import cấu hình tác nghiệp từ template Excel (spec §5.1).

3 sheet theo tên: tacnghiepcan / ketquatacnghiep / tacnghiepcauhinh; header dòng 3,
data từ dòng 4. Upsert theo name, KHÔNG xóa bản ghi vắng mặt. Atomic all-or-nothing.
"""
from __future__ import annotations

import re

from django.db import transaction
from openpyxl import load_workbook

from apps.contact_flow.models import OperationResult, OperationStage, OperationTransition

_SHEET_STAGES = "tacnghiepcan"
_SHEET_RESULTS = "ketquatacnghiep"
_SHEET_RULES = "tacnghiepcauhinh"
_UNIT_MINUTES = {"phút": 1, "phut": 1, "giờ": 60, "gio": 60, "ngày": 1440, "ngay": 1440}
_DELAY_RE = re.compile(r"^\s*(\d+)\s*(\S+)\s*$")


class FlowImportError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("import failed")
        self.errors = errors


def _truthy(value) -> bool:
    return str(value).strip().lower() == "true"


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


class FlowImportService:
    @staticmethod
    def parse_delay(raw) -> int | None:
        text = _text(raw)
        if not text:
            return None
        match = _DELAY_RE.match(text.lower())
        if not match or match.group(2) not in _UNIT_MINUTES:
            raise ValueError(f"Không hiểu thời gian: {text!r} (dạng '30 phút/1 giờ/10 ngày')")
        return int(match.group(1)) * _UNIT_MINUTES[match.group(2)]

    @staticmethod
    def import_workbook(file_obj) -> dict:
        errors: list[dict] = []
        report = {k: {"created": 0, "updated": 0} for k in ("stages", "results", "transitions")}
        # Parse openpyxl (I/O + streaming) NGOÀI transaction: đọc hết 3 sheet vào
        # memory trước, transaction bên dưới chỉ còn các câu ghi DB ngắn.
        workbook = load_workbook(filename=file_obj, read_only=True, data_only=True)
        try:
            sheet_rows = {
                name: FlowImportService._rows(workbook, name, errors)
                for name in (_SHEET_STAGES, _SHEET_RESULTS, _SHEET_RULES)
            }
        finally:
            workbook.close()
        with transaction.atomic():
            stage_map = FlowImportService._import_stages(sheet_rows[_SHEET_STAGES], errors, report)
            result_map = FlowImportService._import_results(sheet_rows[_SHEET_RESULTS], errors, report)
            FlowImportService._import_rules(sheet_rows[_SHEET_RULES], stage_map, result_map, errors, report)
            if errors:
                raise FlowImportError(errors)
        return report

    @staticmethod
    def _rows(workbook, sheet_name, errors):
        if sheet_name not in workbook.sheetnames:
            errors.append({"sheet": sheet_name, "row": 0, "error": "Thiếu sheet này trong file."})
            return []
        rows = workbook[sheet_name].iter_rows(min_row=4, values_only=True)
        return [(i, row) for i, row in enumerate(rows, start=4)]

    @staticmethod
    def _import_stages(rows, errors, report) -> dict[str, OperationStage]:
        stage_map: dict[str, OperationStage] = {}
        start_names: list[str] = []
        for row_no, row in rows:
            # Cột: STT | Tên | Màu nền | Nhãn KH | KhoiDau | Kho số | Kho số phút | Sửa giờ
            name = _text(row[1] if len(row) > 1 else None)
            if not name:
                continue
            defaults = {
                "color": _text(row[2] if len(row) > 2 else None),
                "customer_label": _text(row[3] if len(row) > 3 else None),
                "is_start": _truthy(row[4] if len(row) > 4 else None),
                "pool_enabled": _truthy(row[5] if len(row) > 5 else None),
                "pool_minutes": int(row[6] or 0) if len(row) > 6 and row[6] is not None else 0,
                "allow_edit_due": _truthy(row[7] if len(row) > 7 else None),
                "position": row_no,
                "is_active": True,
            }
            stage, created = OperationStage.objects.update_or_create(name=name, defaults=defaults)
            stage_map[name] = stage
            report["stages"]["created" if created else "updated"] += 1
            if defaults["is_start"]:
                start_names.append(name)
        if len(start_names) > 1:
            errors.append({
                "sheet": _SHEET_STAGES, "row": 0,
                "error": f"Có {len(start_names)} bước KhoiDau=true ({', '.join(start_names)}); chỉ được 1.",
            })
        return stage_map

    @staticmethod
    def _import_results(rows, errors, report) -> dict[str, OperationResult]:
        result_map: dict[str, OperationResult] = {}
        for row_no, row in rows:
            # Cột: STT | Tên | Màu nền | Chốt đơn | Đặt lịch
            name = _text(row[1] if len(row) > 1 else None)
            if not name:
                continue
            defaults = {
                "color": _text(row[2] if len(row) > 2 else None),
                "is_close": _truthy(row[3] if len(row) > 3 else None),
                "requires_schedule": _truthy(row[4] if len(row) > 4 else None),
                "position": row_no,
                "is_active": True,
            }
            result, created = OperationResult.objects.update_or_create(name=name, defaults=defaults)
            result_map[name] = result
            report["results"]["created" if created else "updated"] += 1
        return result_map

    @staticmethod
    def _import_rules(rows, stage_map, result_map, errors, report) -> None:
        # Tên có thể đã tồn tại trong DB từ lần import trước dù vắng trong file này.
        for stage in OperationStage.objects.all():
            stage_map.setdefault(stage.name, stage)
        for result in OperationResult.objects.all():
            result_map.setdefault(result.name, result)
        for row_no, row in rows:
            # Cột: STT | Nếu | Kết quả | Thì | Sau bao lâu
            from_name = _text(row[1] if len(row) > 1 else None)
            if not from_name:
                continue
            result_name = _text(row[2] if len(row) > 2 else None)
            to_name = _text(row[3] if len(row) > 3 else None)
            from_stage = stage_map.get(from_name)
            result = result_map.get(result_name)
            to_stage = stage_map.get(to_name) if to_name else None
            if from_stage is None:
                errors.append({"sheet": _SHEET_RULES, "row": row_no,
                               "error": f"Tác nghiệp 'Nếu' không tồn tại: {from_name!r}"})
                continue
            if result is None:
                errors.append({"sheet": _SHEET_RULES, "row": row_no,
                               "error": f"Kết quả không tồn tại: {result_name!r}"})
                continue
            if to_name and to_stage is None:
                errors.append({"sheet": _SHEET_RULES, "row": row_no,
                               "error": f"Tác nghiệp 'Thì' không tồn tại: {to_name!r}"})
                continue
            try:
                delay = FlowImportService.parse_delay(row[4] if len(row) > 4 else None)
            except ValueError as exc:
                errors.append({"sheet": _SHEET_RULES, "row": row_no, "error": str(exc)})
                continue
            if to_stage is not None and not delay:
                errors.append({"sheet": _SHEET_RULES, "row": row_no,
                               "error": "Có bước 'Thì' thì bắt buộc có 'Sau bao lâu' ≥ 1 phút."})
                continue
            if to_stage is None:
                delay = None
            _, created = OperationTransition.objects.update_or_create(
                from_stage=from_stage, result=result,
                defaults={"to_stage": to_stage, "delay_minutes": delay},
            )
            report["transitions"]["created" if created else "updated"] += 1
