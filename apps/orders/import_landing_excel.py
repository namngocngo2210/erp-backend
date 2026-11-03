from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from openpyxl import Workbook, load_workbook

from .import_excel import (
    STAFF_COLUMN_IGNORED_WARNING,
    ImportedOrderFailure,
    _flatten_serializer_errors,
    _normalize_channel_code_or_original,
    _normalize_header,
    resolve_import_staff,
)
from .models import OrderSource
from .serializers import OrderCreateSerializer
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.models import User

logger = logging.getLogger(__name__)

LANDING_IMPORT_TEMPLATE_HEADERS = [
    "Kênh Bán",
    "Mã nhân viên",
    "Thời gian",
    "Tên KH",
    "SĐT",
    "Địa chỉ",
    "Tên SP",
]

LANDING_IMPORT_TEXT_FIELDS = [
    "channel_code",
    "staff_username",
    "created_at",
    "name",
    "phone",
    "raw_address",
    "product_raw",
]

LANDING_IMPORT_TEMPLATE_SAMPLE_ROW = {
    "Kênh Bán": "Facebook",
    "Mã nhân viên": "admin",
    "Thời gian": "20/04/2026 09:30:00",
    "Tên KH": "Nguyen Van A",
    "SĐT": "0909123456",
    "Địa chỉ": "123 Nguyen Trai, Quan 1, TP HCM",
    "Tên SP": "Bộ nồi cao cấp 5 món",
}

LANDING_HEADER_ALIASES = {
    "kênh bán": "channel_code",
    "kenh ban": "channel_code",
    "channel_code": "channel_code",
    "mã nhân viên": "staff_username",
    "ma nhan vien": "staff_username",
    "staff_username": "staff_username",
    "username": "staff_username",
    "thời gian": "created_at",
    "thoi gian": "created_at",
    "created_at": "created_at",
    "tên kh": "name",
    "ten kh": "name",
    "name": "name",
    "sđt": "phone",
    "sdt": "phone",
    "phone": "phone",
    "địa chỉ": "raw_address",
    "dia chi": "raw_address",
    "raw_address": "raw_address",
    "tên sp": "product_raw",
    "ten sp": "product_raw",
    "product_raw": "product_raw",
}

NORMALIZED_LANDING_HEADER_ALIASES = {
    _normalize_header(k): v for k, v in LANDING_HEADER_ALIASES.items()
}


def build_landing_orders_import_template() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "landing_orders_import"
    worksheet.append(LANDING_IMPORT_TEMPLATE_HEADERS)
    worksheet.append(
        [LANDING_IMPORT_TEMPLATE_SAMPLE_ROW.get(header, "") for header in LANDING_IMPORT_TEMPLATE_HEADERS]
    )
    worksheet.freeze_panes = "A2"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output.getvalue()


def _build_landing_header_index(headers: list[str]) -> dict[str, int]:
    index_map: dict[str, int] = {}
    for index, raw_header in enumerate(headers):
        field_name = NORMALIZED_LANDING_HEADER_ALIASES.get(_normalize_header(raw_header))
        if not field_name or field_name in index_map:
            continue
        index_map[field_name] = index
    return index_map


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


_EXCEL_SERIAL_PATTERN = re.compile(r"^\d+([.,]\d+)?$")
_EXCEL_EPOCH = datetime(1899, 12, 30)
# Ngưỡng năm tối thiểu hợp lệ cho created_at — hệ thống không có đơn trước 2000,
# nên mọi năm nhỏ hơn chắc chắn là dữ liệu parse lỗi (vd "0202").
_MIN_VALID_YEAR = 2000


def _parse_created_at(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = _EXCEL_EPOCH + timedelta(days=float(value))
    else:
        text = _cell_text(value)
        if not text:
            return None
        dt = None
        # Excel serial date (e.g. "46138,57319" or "46138.57319"). Days since
        # 1899-12-30, fractional part = fraction of day. Locale-aware comma.
        if _EXCEL_SERIAL_PATTERN.match(text):
            try:
                dt = _EXCEL_EPOCH + timedelta(days=float(text.replace(",", ".")))
            except (TypeError, ValueError, OverflowError):
                dt = None
        # Try RFC 2822 / RFC 1123 ("Mon, 20 Apr 2026 10:54:26 GMT") —
        # locale-independent via email.utils, avoids %a/%b pitfalls when the
        # server locale is not English.
        if dt is None and "," in text and ("GMT" in text or "+" in text or "-" in text[-6:]):
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                dt = None
        # ISO 8601 incl. "Z" / microseconds / offset (e.g. "2026-04-26T06:49:48.000Z").
        if dt is None and ("T" in text or "Z" in text):
            try:
                dt = parse_datetime(text)
            except (TypeError, ValueError):
                dt = None
        if dt is None:
            for fmt in (
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%d/%m/%Y",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%Y-%m-%dT%H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
                "%Y/%m/%d",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M",
                "%d-%m-%Y",
                "%m-%d-%Y %H:%M:%S",
                "%m-%d-%Y %H:%M",
                "%m-%d-%Y",
            ):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            raise ValueError(f"Thời gian không hợp lệ: {text}. Định dạng đúng: dd/mm/yyyy hh:mm:ss")
    # Chặn năm vô lý (vd parse lỗi ra năm 0202): trả None để fallback về
    # auto_now_add (now()) thay vì ghi rác vào created_at — tránh đơn bị lọt
    # ra ngoài mọi khoảng ngày và "có trong DB mà search không ra".
    if not (_MIN_VALID_YEAR <= dt.year <= timezone.now().year + 1):
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def build_landing_payload_from_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Build an OrderCreateSerializer-ready payload from a field-keyed dict.

    Used by Excel, text and API (Lark) ingest paths. Field names correspond
    to LANDING_IMPORT_TEXT_FIELDS (channel_code, staff_username, created_at,
    name, phone, raw_address, product_raw). The Lark API path may additionally
    pass ``order_source`` (defaults to "landing" when absent, e.g. Excel/text).
    """
    channel_raw = _cell_text(fields.get("channel_code"))
    if not channel_raw:
        raise ValueError("Thiếu cột Kênh Bán.")
    channel_code = _normalize_channel_code_or_original(channel_raw)

    phone = _cell_text(fields.get("phone"))
    if not phone:
        raise ValueError("Thiếu cột SĐT.")

    created_at_value = _parse_created_at(fields.get("created_at"))

    order_source = OrderSource.normalize(fields.get("order_source"), default=OrderSource.LANDING)

    payload: dict[str, Any] = {
        "channel_code": channel_code,
        "name": _cell_text(fields.get("name")),
        "phone": phone,
        "raw_address": _cell_text(fields.get("raw_address")),
        "product_raw": _cell_text(fields.get("product_raw")),
        "address": "",
        "items": [],
        "order_source": order_source,
        "show_orders": False,
    }
    if created_at_value is not None:
        payload["created_at_override"] = created_at_value

    staff_username = _cell_text(fields.get("staff_username"))
    if staff_username:
        user = User.objects.filter(
            username__iexact=staff_username, deleted_at__isnull=True
        ).first()
        if user is not None:
            payload["staff_id"] = user.id

    return payload


def _build_landing_row_payload(
    row: tuple[Any, ...],
    header_index: dict[str, int],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for field, idx in header_index.items():
        if idx < len(row):
            fields[field] = row[idx]
    return build_landing_payload_from_fields(fields)


def import_landing_orders_from_workbook(*, file_obj, actor) -> dict[str, Any]:
    workbook = load_workbook(filename=file_obj, read_only=True, data_only=True)
    worksheet = workbook.active
    rows_iter = worksheet.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        return {
            "total_rows": 0,
            "success_count": 0,
            "failed_count": 0,
            "failures": [],
            "warnings": [],
        }

    headers = [str(cell or "").strip() for cell in header_row]
    header_index = _build_landing_header_index(headers)

    failures: list[ImportedOrderFailure] = []
    success_count = 0
    total_rows = 0
    staff_column_ignored = False

    for row_number, row in enumerate(rows_iter, start=2):
        if not any(str(cell or "").strip() for cell in row):
            continue

        total_rows += 1

        try:
            row_payload = _build_landing_row_payload(row, header_index)
        except Exception as exc:
            logger.debug("Landing import row %s build failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Dữ liệu dòng không hợp lệ.",
                )
            )
            continue

        serializer = OrderCreateSerializer(
            data=row_payload,
            context={"skip_channel_check": True},
        )
        if not serializer.is_valid():
            logger.debug(
                "Landing import row %s serializer invalid: errors=%s", row_number, serializer.errors
            )
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=_flatten_serializer_errors(serializer.errors),
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        validated = serializer.validated_data
        if resolve_import_staff(validated, actor):
            staff_column_ignored = True

        from apps.orders.application.services.lead_filter_service import LeadFilterService

        decision = LeadFilterService.evaluate(row_payload.get("phone", ""), validated["channel"])
        if not decision.accepted:
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=decision.reason,
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        try:
            OrderService.create_order_manual(validated, actor, created_via="excel_import")
        except Exception as exc:
            logger.debug("Landing import row %s create failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Không thể import dòng này.",
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        success_count += 1

    return {
        "total_rows": total_rows,
        "success_count": success_count,
        "failed_count": len(failures),
        "failures": [failure.to_payload() for failure in failures],
        "warnings": [STAFF_COLUMN_IGNORED_WARNING] if staff_column_ignored else [],
    }


def import_landing_orders_from_text(*, text_data: str, actor) -> dict[str, Any]:
    """Import landing orders from pipe-delimited text.

    Field order: channel_code | staff_username | created_at | name | phone | raw_address | product_raw
    """
    lines = [line.strip() for line in text_data.strip().split("\n") if line.strip()]
    if not lines:
        return {
            "total_rows": 0,
            "success_count": 0,
            "failed_count": 0,
            "failures": [],
            "warnings": [],
        }

    header_index = {field: idx for idx, field in enumerate(LANDING_IMPORT_TEXT_FIELDS)}
    failures: list[ImportedOrderFailure] = []
    success_count = 0
    total_rows = 0
    staff_column_ignored = False

    for row_number, line in enumerate(lines, start=1):
        total_rows += 1

        fields = [field.strip() for field in line.split("|")]
        if len(fields) < len(LANDING_IMPORT_TEXT_FIELDS):
            fields.extend([""] * (len(LANDING_IMPORT_TEXT_FIELDS) - len(fields)))

        try:
            row_payload = _build_landing_row_payload(tuple(fields), header_index)
        except Exception as exc:
            logger.debug("Landing text import row %s build failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Dữ liệu dòng không hợp lệ.",
                )
            )
            continue

        serializer = OrderCreateSerializer(
            data=row_payload,
            context={"skip_channel_check": True},
        )
        if not serializer.is_valid():
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=_flatten_serializer_errors(serializer.errors),
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        validated = serializer.validated_data
        if resolve_import_staff(validated, actor):
            staff_column_ignored = True

        from apps.orders.application.services.lead_filter_service import LeadFilterService

        decision = LeadFilterService.evaluate(row_payload.get("phone", ""), validated["channel"])
        if not decision.accepted:
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=decision.reason,
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        try:
            OrderService.create_order_manual(validated, actor, created_via="excel_import")
        except Exception as exc:
            logger.debug("Landing text import row %s create failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Không thể import dòng này.",
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        success_count += 1

    return {
        "total_rows": total_rows,
        "success_count": success_count,
        "failed_count": len(failures),
        "failures": [failure.to_payload() for failure in failures],
        "warnings": [STAFF_COLUMN_IGNORED_WARNING] if staff_column_ignored else [],
    }
