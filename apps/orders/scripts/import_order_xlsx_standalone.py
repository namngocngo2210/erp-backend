from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import unicodedata

from django.db import IntegrityError, transaction
from django.utils import timezone
from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter

from apps.channels_core.models import Channel
from apps.customers.services import CustomerService
from apps.kiotviet.models import KiotVietChannel
from apps.orders.address_catalog import map_to_catalog
from apps.orders.address_parser import format_standard_address, parse_legacy_level3
from apps.orders.models import Order, OrderItem, OrderStatus, OrderType
from apps.catalog.models import Product
from apps.orders.product_parser import is_trash_sku
from apps.users.models import User

logger = logging.getLogger(__name__)


HEADER_FIELD_MAP = {
    "kênh kiot": "kiot_channel_raw",
    "kenh kiot": "kiot_channel_raw",
    "người tạo": "username",
    "nguoi tao": "username",
    "kênh bán": "channel_raw",
    "kenh ban": "channel_raw",
    "tên kh": "customer_name",
    "ten kh": "customer_name",
    "giới tính": "gender_raw",
    "gioi tinh": "gender_raw",
    "sđt": "phone",
    "sdt": "phone",
    "địa chỉ": "raw_address",
    "dia chi": "raw_address",
    "mã sp": "sku_list",
    "ma sp": "sku_list",
    "sl": "quantity_list",
    "giá": "unit_price_list",
    "gia": "unit_price_list",
    "giảm giá phiếu đặt": "booking_voucher_discount",
    "giam gia phieu dat": "booking_voucher_discount",
    "trọng lượng": "weight",
    "trong luong": "weight",
    "kích thước": "dimensions",
    "kich thuoc": "dimensions",
    "loại đơn": "order_type_raw",
    "loai don": "order_type_raw",
    "mã đặt hàng": "order_code",
    "ma dat hang": "order_code",
    "mã hóa đơn": "invoice_code",
    "ma hoa don": "invoice_code",
    "tổng sau giảm": "total_after_discount",
    "tong sau giam": "total_after_discount",
    "mã vận đơn": "shipping_code",
    "ma van don": "shipping_code",
    "ngày tạo đơn": "created_at_raw",
    "ngay tao don": "created_at_raw",
    "trạng thái": "status_raw",
    "trang thai": "status_raw",
}

# Some headers in source xlsx are corrupted ("System.Xml.XmlElement"), so we map
# by fixed column letters for critical fields.
COLUMN_FALLBACK_FIELD_MAP = {
    "A": "kiot_channel_raw",
    "C": "username",
    "D": "channel_raw",
    "E": "customer_name",
    "F": "gender_raw",
    "G": "phone",
    "H": "raw_address",
    "K": "sku_list",
    "L": "quantity_list",
    "M": "unit_price_list",
    "N": "discount_amount",
    "O": "booking_voucher_discount",
    "P": "weight",
    "Q": "dimensions",
    "R": "branch_id",
    "S": "order_type_raw",
    "X": "order_code",
    "Y": "invoice_code",
    "AA": "total_after_discount",
    "AB": "shipping_code",
    "AC": "created_at_raw",
    "AD": "status_raw",
}

CHANNEL_CODE_MAP = {
    "facebook": "facebook",
    "facebook.cường": "facebook-cuong",
    "facebook.cuong": "facebook-cuong",
    "facebook.lợi": "facebook-loi",
    "facebook.loi": "facebook-loi",
    "google ads/ web": "google-ads",
    "google ads/web": "google-ads",
    "facebook.lan": "facebook-lan",
    "hàng mẫu koc": "old-koc",
    "hang mau koc": "old-koc",
    "google ads": "google-ads",
    "web": "old-web",
    "ban truc tiep": "direct",
    "ban-truc-tiep": "direct",
    "bán trực tiếp": "direct",
}

KIOT_CHANNEL_ID_MAP = {
    "old_kiot": 5,
    "new_kiot": 4,
}

ORDER_TYPE_MAP = {
    "đơn thường": OrderType.NORMAL,
    "don thuong": OrderType.NORMAL,
    "normal": OrderType.NORMAL,
}

STATUS_MAP = {
    "chờ chuyển hoàn": OrderStatus.AWAITING_RETURN_SHIPMENT,
    "cho chuyen hoan": OrderStatus.AWAITING_RETURN_SHIPMENT,
    "chờ xác nhận hoàn": OrderStatus.AWAITING_RETURN_CONFIRMATION,
    "cho xac nhan hoan": OrderStatus.AWAITING_RETURN_CONFIRMATION,
    "chờ chuyển hoàn lại": OrderStatus.AWAITING_REPICKUP,
    "cho chuyen hoan lai": OrderStatus.AWAITING_REPICKUP,
    "chờ giao lại": OrderStatus.AWAITING_REDELIVERY,
    "cho giao lai": OrderStatus.AWAITING_REDELIVERY,
    "đã chuyển hoàn": OrderStatus.RETURNED,
    "da chuyen hoan": OrderStatus.RETURNED,
    "đã hủy": OrderStatus.CANCELLED,
    "da huy": OrderStatus.CANCELLED,
    "đang chuyển hoàn": OrderStatus.IN_RETURN,
    "dang chuyen hoan": OrderStatus.IN_RETURN,
    "đang giao hàng": OrderStatus.IN_DELIVERY,
    "dang giao hang": OrderStatus.IN_DELIVERY,
    "giao thành công": OrderStatus.DELIVERED_SUCCESS,
    "giao thanh cong": OrderStatus.DELIVERED_SUCCESS,
    "thất lạc": OrderStatus.CANCELLED,
    "that lac": OrderStatus.CANCELLED,
    "trả hàng": OrderStatus.RETURNED,
    "tra hang": OrderStatus.RETURNED,
}

GENDER_MAP = {
    "nam": 1,
    "male": 1,
    "1": 1,
    "nữ": 0,
    "nu": 0,
    "female": 0,
    "0": 0,
}


class RowValidationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class RowError:
    excel_row_number: int
    order_code: str
    shipping_code: str
    raw_row_payload: str
    error_code: str
    error_message: str

    def to_csv_row(self) -> dict[str, str | int]:
        return {
            "excel_row_number": self.excel_row_number,
            "order_code": self.order_code,
            "shipping_code": self.shipping_code,
            "raw_row_payload": self.raw_row_payload,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class ImportResult:
    total_rows: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    error_file: str = ""
    summary_file: str = ""


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d")
    return " ".join(text.split())


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value).strip()


def _to_decimal(value: Any, *, field_name: str) -> Decimal:
    if value is None or str(value).strip() == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    raw = str(value).strip().replace(" ", "")
    if not raw:
        return Decimal("0")

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0] != "0":
            raw = raw.replace(".", "")

    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise RowValidationError("invalid_decimal", f"{field_name} không hợp lệ: {value}") from exc


def _to_int(value: Any, *, field_name: str) -> int:
    if value is None or str(value).strip() == "":
        raise RowValidationError("missing_required", f"{field_name} là bắt buộc.")
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError) as exc:
        raise RowValidationError("invalid_integer", f"{field_name} không hợp lệ: {value}") from exc


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise RowValidationError("invalid_datetime", f"created_at không hợp lệ: {value}")


def _normalize_dimensions(value: str) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    parts = [part.strip() for part in text.split(",")]
    if len(parts) == 3 and all(part for part in parts):
        return "x".join(parts)
    return text


def _split_csv_values(value: Any) -> list[str]:
    text = _normalize_text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _build_header_field_index(worksheet) -> dict[str, int]:
    header_index: dict[str, int] = {}
    for index, cell in enumerate(worksheet[1], start=1):
        normalized = _normalize_key(cell.value)
        field_name = HEADER_FIELD_MAP.get(normalized)
        if field_name and field_name not in header_index:
            header_index[field_name] = index
        col_letter = get_column_letter(index)
        fallback_field = COLUMN_FALLBACK_FIELD_MAP.get(col_letter)
        if fallback_field and fallback_field not in header_index:
            header_index[fallback_field] = index
    return header_index


def _cell_value(row_cells: tuple[Any, ...], col_index: int | None) -> Any:
    if not col_index:
        return None
    zero_idx = col_index - 1
    if zero_idx >= len(row_cells):
        return None
    return row_cells[zero_idx]


def _get_required(row_dict: dict[str, Any], key: str, message: str) -> Any:
    value = row_dict.get(key)
    if value is None or _normalize_text(value) == "":
        raise RowValidationError("missing_required", message)
    return value


def _resolve_channel_code(channel_raw: Any) -> str:
    normalized = _normalize_key(channel_raw)
    normalized = normalized.replace(" .", ".").replace(". ", ".")
    normalized = normalized.replace(" /", "/").replace("/ ", "/")
    normalized = normalized.replace(" -", "-").replace("- ", "-")
    normalized = normalized.replace(".-", "-").replace("-.", "-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    if not normalized:
        return "other"
    mapped = CHANNEL_CODE_MAP.get(normalized)
    if mapped:
        return mapped

    fallback = normalized.replace(".", "-").replace("/", "-").replace(" ", "-")
    while "--" in fallback:
        fallback = fallback.replace("--", "-")
    return fallback.strip("-")


def _resolve_order_type(order_type_raw: Any) -> str:
    normalized = _normalize_key(order_type_raw)
    if not normalized:
        return OrderType.NORMAL
    return ORDER_TYPE_MAP.get(normalized, normalized)


def _resolve_status(status_raw: Any) -> int:
    normalized = _normalize_key(status_raw)
    if not normalized:
        return int(OrderStatus.DELIVERED_SUCCESS)

    valid_status_values = {int(choice[0]) for choice in OrderStatus.choices}
    if normalized.isdigit():
        status_value = int(normalized)
        if status_value in valid_status_values:
            return status_value

    if "=>" in normalized:
        left, right = [part.strip() for part in normalized.split("=>", 1)]
        digit_match = re.search(r"\d+", right)
        if digit_match:
            status_value = int(digit_match.group())
            if status_value in valid_status_values:
                return status_value
        normalized = left

    mapped = STATUS_MAP.get(normalized)
    if mapped is None:
        raise RowValidationError("status_mapping_not_found", f"Không map được status: {status_raw}")
    return int(mapped)


def _resolve_gender(gender_raw: Any) -> int | None:
    normalized = _normalize_key(gender_raw)
    if not normalized:
        return None
    return GENDER_MAP.get(normalized)


def _resolve_kiot_channel_id(kiot_raw: Any) -> int | None:
    normalized = _normalize_key(kiot_raw)
    if not normalized:
        return None
    channel_id = KIOT_CHANNEL_ID_MAP.get(normalized)
    if channel_id is None:
        raise RowValidationError("kiot_channel_mapping_not_found", f"Không map được kênh kiot: {kiot_raw}")
    return channel_id


def _build_item_payloads(row_dict: dict[str, Any]) -> list[dict[str, Any]]:
    skus = _split_csv_values(row_dict.get("sku_list"))
    quantities = _split_csv_values(row_dict.get("quantity_list"))
    unit_prices = _split_csv_values(row_dict.get("unit_price_list"))

    if not skus:
        skus = ["BNC02"]
        quantities = [quantities[0] if quantities else "1"]
        unit_prices = [unit_prices[0] if unit_prices else "0"]
    target_len = max(len(skus), len(quantities), len(unit_prices))
    while len(skus) < target_len:
        skus.append("BNC02")
    while len(quantities) < target_len:
        quantities.append("0")
    while len(unit_prices) < target_len:
        unit_prices.append("0")

    items: list[dict[str, Any]] = []
    for idx, sku in enumerate(skus):
        quantity = _to_int(quantities[idx], field_name=f"quantity[{idx}]")
        unit_price = _to_decimal(unit_prices[idx], field_name=f"unit_price[{idx}]")
        items.append(
            {
                "sku": sku,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": unit_price * Decimal(quantity),
            }
        )
    return items


def _raw_payload_by_column(row_cells: tuple[Any, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for index, value in enumerate(row_cells, start=1):
        payload[get_column_letter(index)] = _normalize_text(value)
    return payload


def _row_looks_empty(row_cells: tuple[Any, ...]) -> bool:
    return not any(_normalize_text(value) for value in row_cells)


def _build_fallback_phone(*parts: Any) -> str:
    seed = "|".join(_normalize_text(part) for part in parts if _normalize_text(part))
    if not seed:
        seed = "import-order-fallback-phone"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    number = int(digest[:8], 16) % 100000000
    return f"09{number:08d}"


def _resolve_staff_user(username_or_display_name: str) -> User | None:
    # 1) Exact username match first
    staff = User.objects.filter(
        username=username_or_display_name,
        deleted_at__isnull=True,
    ).first()
    if staff is not None:
        return staff

    # 2) Fallback by display_name (case-insensitive)
    staff = User.objects.filter(
        display_name__iexact=username_or_display_name,
        deleted_at__isnull=True,
    ).first()
    if staff is not None:
        return staff

    # 3) Additional fallback by first_name (case-insensitive)
    return User.objects.filter(
        first_name__iexact=username_or_display_name,
        deleted_at__isnull=True,
    ).first()


def _upsert_order(
    *,
    row_dict: dict[str, Any],
    dry_run: bool,
) -> str:
    order_code = _normalize_text(row_dict.get("order_code"))
    username = _normalize_text(row_dict.get("username")) or "admin"
    phone = _normalize_text(row_dict.get("phone"))

    status = _resolve_status(row_dict.get("status_raw"))
    channel_code = _resolve_channel_code(row_dict.get("channel_raw"))
    channel = Channel.objects.filter(code=channel_code, deleted_at__isnull=True, is_active=True).first()
    if channel is None:
        raise RowValidationError("channel_not_found", f"Không tìm thấy channel code: {channel_code}")

    staff = _resolve_staff_user(username)
    if staff is None:
        raise RowValidationError("user_not_found", f"Không tìm thấy user username: {username}")

    kiot_channel_id = _resolve_kiot_channel_id(row_dict.get("kiot_channel_raw"))
    if kiot_channel_id is not None and not KiotVietChannel.objects.filter(id=kiot_channel_id).exists():
        raise RowValidationError("kiot_channel_not_found", f"Không tìm thấy KiotVietChannel id={kiot_channel_id}")

    items = _build_item_payloads(row_dict)
    total_amount = sum((item["line_total"] for item in items), Decimal("0"))

    discount_amount = _to_decimal(row_dict.get("discount_amount"), field_name="discount_amount")
    booking_voucher_discount = _to_decimal(
        row_dict.get("booking_voucher_discount"),
        field_name="booking_voucher_discount",
    )
    total_after_discount_raw = row_dict.get("total_after_discount")
    if _normalize_text(total_after_discount_raw):
        try:
            total_after_discount = _to_decimal(total_after_discount_raw, field_name="total_after_discount")
        except RowValidationError as exc:
            if exc.code != "invalid_decimal":
                raise
            # Fallback: when AA is corrupted text (e.g. "lặp"), derive from price columns.
            total_after_discount = total_amount
    else:
        total_after_discount = total_amount - discount_amount - booking_voucher_discount

    try:
        weight = _to_decimal(row_dict.get("weight"), field_name="weight")
    except RowValidationError as exc:
        if exc.code != "invalid_decimal":
            raise
        weight = Decimal("5300")
    dimensions = _normalize_dimensions(_normalize_text(row_dict.get("dimensions")))
    order_type = _resolve_order_type(row_dict.get("order_type_raw"))
    created_at = _parse_datetime(row_dict.get("created_at_raw"))
    gender = _resolve_gender(row_dict.get("gender_raw"))
    raw_address = _normalize_text(row_dict.get("raw_address"))
    normalized_address = parse_legacy_level3(raw_address)
    mapped_input = {
        "address": normalized_address.get("address", ""),
        "ward": normalized_address.get("ward", ""),
        "district": normalized_address.get("district", ""),
        "province": normalized_address.get("province", ""),
        "ward_code": _normalize_text(normalized_address.get("ward_code")),
        "district_code": _normalize_text(normalized_address.get("district_code")),
        "province_code": _normalize_text(normalized_address.get("province_code")),
    }
    mapped_address = map_to_catalog(mapped_input)
    resolved_ward = mapped_address.get("ward") or normalized_address.get("ward") or ""
    resolved_district = mapped_address.get("district") or normalized_address.get("district") or ""
    resolved_province = mapped_address.get("province") or normalized_address.get("province") or ""
    resolved_ward_code = mapped_address.get("ward_code") or mapped_input.get("ward_code") or ""
    resolved_district_code = mapped_address.get("district_code") or mapped_input.get("district_code") or ""
    resolved_province_code = mapped_address.get("province_code") or mapped_input.get("province_code") or ""
    resolved_address = format_standard_address(normalized_address)

    # Resolve KiotViet location/ward
    from apps.orders.utils.kiotviet_address_mapping import resolve_kiotviet_address
    kv_resolved = resolve_kiotviet_address(normalized_address, raw_address=raw_address)

    customer_name = _normalize_text(row_dict.get("customer_name"))
    if not phone:
        phone = _build_fallback_phone(order_code, row_dict.get("shipping_code"), customer_name, "missing-phone")
    try:
        customer = CustomerService.get_or_create_by_phone(phone=phone, full_name=customer_name)
    except ValueError as exc:
        if "Invalid phone payload" not in str(exc):
            raise
        fallback_phone = _build_fallback_phone(order_code, row_dict.get("shipping_code"), phone)
        customer = CustomerService.get_or_create_by_phone(phone=fallback_phone, full_name=customer_name)
    if gender is not None and customer.gender != gender:
        customer.gender = gender
        customer.save(update_fields=["gender", "updated_at"])

    existing_order = None
    if order_code:
        existing_order = Order.objects.filter(order_code=order_code, deleted_at__isnull=True).first()
    action = "updated" if existing_order is not None else "created"

    if dry_run:
        return action

    with transaction.atomic():
        order = existing_order
        if order is None:
            order = Order(
                order_code=order_code,
                channel=channel,
                customer=customer,
            )

        order.kiotviet_channel_id = kiot_channel_id
        order.staff = staff
        order.name = _normalize_text(row_dict.get("customer_name"))
        order.phone = phone
        order.raw_address = raw_address
        order.address = resolved_address
        order.ward = resolved_ward
        order.district = resolved_district
        order.province = resolved_province
        order.ward_code = resolved_ward_code
        order.district_code = resolved_district_code
        order.province_code = resolved_province_code
        order.kiotviet_location = kv_resolved.get("locationName", "")
        order.kiotviet_ward = kv_resolved.get("wardName", "")
        order.status = status
        order.discount_amount = discount_amount
        order.booking_voucher_discount = booking_voucher_discount
        order.total_amount = total_amount
        order.total_after_discount = total_after_discount
        order.weight = weight
        order.dimensions = dimensions
        order.branch_id = _normalize_text(row_dict.get("branch_id"))
        order.order_type = order_type
        order.invoice_code = _normalize_text(row_dict.get("invoice_code"))
        order.shipping_code = _normalize_text(row_dict.get("shipping_code"))
        order.quantity = sum(item["quantity"] for item in items)
        order.sku = items[0]["sku"] if items else ""
        order.product = ", ".join(f"{item['sku']} x{item['quantity']}" for item in items)
        order.save()

        if created_at is not None:
            created_at_tz = timezone.make_aware(created_at) if timezone.is_naive(created_at) else created_at
            Order.objects.filter(pk=order.pk).update(created_at=created_at_tz)

        order.items.all().delete()
        order_item_objects: list[OrderItem] = []
        for item in items:
            product = Product.objects.filter(sku=item["sku"]).first()
            if product is None:
                product = Product.objects.create(
                    name=item["sku"],
                    sku=item["sku"],
                    price=item["unit_price"],
                    is_active=True,
                )
            elif not product.is_active and not is_trash_sku(product.sku):
                product.is_active = True
                product.deleted_at = None
                product.save(update_fields=["is_active", "deleted_at", "updated_at"])
            elif not product.is_active:
                # Soft-deleted trash/old SKU (see migration 0079): keep it
                # deleted instead of resurrecting it. The order item still
                # links to this existing product.
                logger.warning("Import skipped resurrecting trash SKU=%s", product.sku)

            order_item_objects.append(
                OrderItem(
                    order=order,
                    product=product,
                    product_name=product.name,
                    sku=item["sku"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    line_total=item["line_total"],
                )
            )
        if order_item_objects:
            OrderItem.objects.bulk_create(order_item_objects)
    return action


def _default_output_paths(
    *,
    file_path: Path,
    error_file: str | None,
    summary_file: str | None,
) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = file_path.parent

    error_path = Path(error_file) if error_file else output_dir / f"import_orders_errors_{timestamp}.csv"
    summary_path = Path(summary_file) if summary_file else output_dir / f"import_orders_summary_{timestamp}.json"
    return error_path, summary_path


def import_orders_from_xlsx(
    *,
    file_path: str,
    dry_run: bool,
    error_file: str | None = None,
    summary_file: str | None = None,
) -> ImportResult:
    workbook_path = Path(file_path)
    if not workbook_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

    error_path, summary_path = _default_output_paths(
        file_path=workbook_path,
        error_file=error_file,
        summary_file=summary_file,
    )
    error_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    result = ImportResult(error_file=str(error_path), summary_file=str(summary_path))
    row_errors: list[RowError] = []

    workbook = load_workbook(filename=str(workbook_path), read_only=True, data_only=True)
    sheet = workbook.active
    header_index = _build_header_field_index(sheet)

    for row_number, row_cells in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if _row_looks_empty(row_cells):
            continue

        result.total_rows += 1
        row_raw_payload_dict = _raw_payload_by_column(row_cells)
        order_code = _normalize_text(row_raw_payload_dict.get("X"))
        shipping_code = _normalize_text(row_raw_payload_dict.get("AB"))

        row_dict = {
            field_name: _cell_value(row_cells, col_idx)
            for field_name, col_idx in header_index.items()
        }

        try:
            action = _upsert_order(row_dict=row_dict, dry_run=dry_run)
            if action == "created":
                result.created_count += 1
            else:
                result.updated_count += 1
        except RowValidationError as exc:
            result.skipped_count += 1
            row_errors.append(
                RowError(
                    excel_row_number=row_number,
                    order_code=order_code,
                    shipping_code=shipping_code,
                    raw_row_payload=json.dumps(row_raw_payload_dict, ensure_ascii=False),
                    error_code=exc.code,
                    error_message=exc.message,
                )
            )
        except (IntegrityError, ValueError) as exc:
            result.skipped_count += 1
            row_errors.append(
                RowError(
                    excel_row_number=row_number,
                    order_code=order_code,
                    shipping_code=shipping_code,
                    raw_row_payload=json.dumps(row_raw_payload_dict, ensure_ascii=False),
                    error_code="database_error",
                    error_message=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            result.skipped_count += 1
            row_errors.append(
                RowError(
                    excel_row_number=row_number,
                    order_code=order_code,
                    shipping_code=shipping_code,
                    raw_row_payload=json.dumps(row_raw_payload_dict, ensure_ascii=False),
                    error_code="unexpected_error",
                    error_message=str(exc),
                )
            )

    result.error_count = len(row_errors)

    with error_path.open("w", newline="", encoding="utf-8-sig") as file_handle:
        writer = csv.DictWriter(
            file_handle,
            fieldnames=[
                "excel_row_number",
                "order_code",
                "shipping_code",
                "raw_row_payload",
                "error_code",
                "error_message",
            ],
        )
        writer.writeheader()
        for row_error in row_errors:
            writer.writerow(row_error.to_csv_row())

    summary_payload = {
        "file_path": str(workbook_path),
        "dry_run": dry_run,
        "total_rows": result.total_rows,
        "created_count": result.created_count,
        "updated_count": result.updated_count,
        "skipped_count": result.skipped_count,
        "error_count": result.error_count,
        "error_file": result.error_file,
    }
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import standalone orders from XLSX file.")
    parser.add_argument("--file", required=True, help="Path to xlsx file.")
    parser.add_argument("--error-file", default=None, help="Path to output errors csv file.")
    parser.add_argument("--summary-file", default=None, help="Path to output summary json file.")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no database write.")
    parser.add_argument("--apply", action="store_true", help="Apply data changes to database.")
    return parser


def _parse_args(raw_args: list[str]) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(raw_args)
    if args.dry_run and args.apply:
        parser.error("Chỉ chọn một trong --dry-run hoặc --apply.")
    if not args.dry_run and not args.apply:
        parser.error("Bắt buộc chọn --dry-run hoặc --apply.")
    return args


def run(*script_args: str) -> dict[str, Any]:
    args = _parse_args(list(script_args))
    result = import_orders_from_xlsx(
        file_path=args.file,
        dry_run=bool(args.dry_run and not args.apply),
        error_file=args.error_file,
        summary_file=args.summary_file,
    )
    payload = {
        "total_rows": result.total_rows,
        "created_count": result.created_count,
        "updated_count": result.updated_count,
        "skipped_count": result.skipped_count,
        "error_count": result.error_count,
        "error_file": result.error_file,
        "summary_file": result.summary_file,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload
