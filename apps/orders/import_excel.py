from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
import logging

from django.utils.text import slugify
from openpyxl import Workbook, load_workbook

from .models import OrderType
from apps.catalog.models import Product
from .product_parser import is_trash_sku
from .serializers import OrderCreateSerializer
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.models import User
from apps.users.permissions import can_assign_sale

from ._import_excel_constants import (
    IMPORT_PAYLOAD_FIELDS,
    IMPORT_TEMPLATE_HEADERS,
    IMPORT_TEMPLATE_SAMPLE_ROW,
    KOC_IMPORT_PAYLOAD_FIELDS,
    NORMALIZED_CHANNEL_CODE_MAP,
    NORMALIZED_HEADER_ALIASES,
    NORMALIZED_KIOT_CHANNEL_ID_MAP,
    _normalize_key,
)

logger = logging.getLogger(__name__)

STAFF_COLUMN_IGNORED_WARNING = (
    "Cột Nhân viên bị bỏ qua: bạn không có quyền gán sale — "
    "toàn bộ đơn trong file đã gán cho bạn."
)


def resolve_import_staff(validated: dict, actor) -> bool:
    """Chốt `validated["staff"]` theo quyền của người import.

    Trả True nếu cột Nhân viên trong file bị bỏ qua (để caller gom cảnh báo).
    Không có quyền mà im lặng gán sai chủ đơn thì import 500 dòng xong mới phát
    hiện, mất cả buổi đối soát.
    """
    requested = validated.get("staff")
    if can_assign_sale(actor):
        if requested is None:
            validated["staff"] = actor
        return False
    ignored = requested is not None and requested.id != getattr(actor, "id", None)
    validated["staff"] = actor
    return ignored



@dataclass
class ImportedOrderFailure:
    row_number: int
    error: str
    phone: str = ""
    name: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "error": self.error,
            "phone": self.phone,
            "name": self.name,
        }


def build_orders_import_template() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "orders_import"
    worksheet.append(IMPORT_TEMPLATE_HEADERS)
    worksheet.append([IMPORT_TEMPLATE_SAMPLE_ROW.get(header, "") for header in IMPORT_TEMPLATE_HEADERS])
    worksheet.freeze_panes = "A2"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output.getvalue()


def import_orders_from_workbook(
    *,
    file_obj,
    actor,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import orders from an .xlsx file.

    `extra_payload` (optional) is merged into every row payload BEFORE
    serializer validation. Used by the landing-orders import path to force
    `order_source="landing"` and `show_orders=False` on every imported row
    while reusing the full 17-column orders template.
    """
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
    header_index = _build_header_index(headers)

    failures: list[ImportedOrderFailure] = []
    success_count = 0
    total_rows = 0
    staff_column_ignored = False

    for row_number, row in enumerate(rows_iter, start=2):
        if not any(str(cell or "").strip() for cell in row):
            continue
        try:
            row_payload = _build_row_payload(row, header_index, row_number=row_number)
            if extra_payload:
                row_payload.update(extra_payload)
        except Exception as exc:
            logger.debug("Import row %s payload build failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Dữ liệu dòng không hợp lệ.",
                )
            )
            continue
        total_rows += 1
        logger.debug(
            "Import row %s payload parsed: phone=%s channel_code=%s kiotviet_channel_id=%s order_type=%s items=%s discount=%s booking_voucher_discount=%s",
            row_number,
            row_payload.get("phone"),
            row_payload.get("channel_code"),
            row_payload.get("kiotviet_channel_id"),
            row_payload.get("order_type"),
            len(row_payload.get("items") or []),
            row_payload.get("discount_amount"),
            row_payload.get("booking_voucher_discount"),
        )
        try:
            _ensure_products_exist(row_payload.get("items") or [], row_number=row_number)
        except Exception as exc:
            logger.debug("Import row %s ensure products failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Không thể tạo sản phẩm mới từ dữ liệu dòng này.",
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        serializer = OrderCreateSerializer(data=row_payload)
        if not serializer.is_valid():
            logger.debug(
                "Import row %s serializer invalid: errors=%s payload_keys=%s",
                row_number,
                serializer.errors,
                sorted(row_payload.keys()),
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
        channel = validated["channel"]
        if not OrderService.can_edit_channel(actor, channel.code):
            logger.debug(
                "Import row %s permission denied for actor=%s channel=%s",
                row_number,
                getattr(actor, "id", None),
                channel.code,
            )
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error="Bạn không có quyền tạo đơn cho kênh này.",
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        if resolve_import_staff(validated, actor):
            staff_column_ignored = True

        try:
            OrderService.create_order_manual(validated, actor, created_via="excel_import")
        except Exception as exc:
            logger.debug(
                "Import row %s create_order_manual failed: %s",
                row_number,
                exc,
                exc_info=True,
            )
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
        logger.debug(
            "Import row %s success: phone=%s channel=%s",
            row_number,
            row_payload.get("phone"),
            channel.code,
        )

    return {
        "total_rows": total_rows,
        "success_count": success_count,
        "failed_count": len(failures),
        "failures": [failure.to_payload() for failure in failures],
        "warnings": [STAFF_COLUMN_IGNORED_WARNING] if staff_column_ignored else [],
    }


def _build_row_payload(row: tuple[Any, ...], header_index: dict[str, int], row_number: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in IMPORT_PAYLOAD_FIELDS:
        index = header_index.get(field_name)
        if index is None:
            continue
        payload[field_name] = _normalize_cell_value(row[index] if index < len(row) else None)

    # customer_code (Mã KH) chỉ có trong layout KOC; passthrough để service khớp KH theo code.
    customer_code_index = header_index.get("customer_code")
    if customer_code_index is not None:
        customer_code = _normalize_cell_value(
            row[customer_code_index] if customer_code_index < len(row) else None
        )
        if customer_code not in ("", None):
            payload["customer_code"] = str(customer_code).strip()

    kiotviet_channel_id = payload.get("kiotviet_channel_id")
    if kiotviet_channel_id not in ("", None):
        normalized_kiot = _normalize_kiot_channel_id_or_original(kiotviet_channel_id)
        logger.debug(
            "Import row %s kiot mapping: raw=%s normalized=%s",
            row_number if row_number is not None else "?",
            kiotviet_channel_id,
            normalized_kiot,
        )
        if normalized_kiot is None:
            payload.pop("kiotviet_channel_id", None)
        else:
            payload["kiotviet_channel_id"] = normalized_kiot
    else:
        payload.pop("kiotviet_channel_id", None)

    staff_username = payload.get("staff_username")
    if staff_username not in ("", None):
        normalized_username = str(staff_username).strip()
        logger.debug(
            "Import row %s staff lookup: username=%s",
            row_number if row_number is not None else "?",
            normalized_username,
        )
        try:
            # Chỉ nhận staff hợp lệ đúng ràng buộc của OrderCreateSerializer (không phải
            # admin/role hệ thống, đang active). Người tạo là admin → coi như không tìm
            # thấy và fallback về người thao tác, tránh fail cả dòng.
            # NOTE: thay filter role=STAFF cũ bằng "loại user có role hệ thống".
            user = (
                User.objects.filter(
                    username__iexact=normalized_username,
                    is_active=True,
                    deleted_at__isnull=True,
                )
                .exclude(role_assignments__role__is_system=True)
                .first()
            )
            if user:
                payload["staff_id"] = user.id
                logger.debug(
                    "Import row %s staff found: username=%s staff_id=%s",
                    row_number if row_number is not None else "?",
                    normalized_username,
                    user.id,
                )
            else:
                logger.warning(
                    "Import row %s staff not found: username=%s",
                    row_number if row_number is not None else "?",
                    normalized_username,
                )
                payload.pop("staff_username", None)
        except Exception as exc:
            logger.warning(
                "Import row %s staff lookup failed: username=%s error=%s",
                row_number if row_number is not None else "?",
                normalized_username,
                exc,
            )
            payload.pop("staff_username", None)
    else:
        payload.pop("staff_username", None)

    channel_code = payload.get("channel_code")
    if channel_code not in ("", None):
        mapped_channel_code = _normalize_channel_code_or_original(channel_code)
        logger.debug(
            "Import row %s channel mapping: raw=%s normalized=%s",
            row_number if row_number is not None else "?",
            channel_code,
            mapped_channel_code,
        )
        payload["channel_code"] = mapped_channel_code
    else:
        payload.pop("channel_code", None)

    decimal_defaults = {
        "discount_amount": "0.00",
        "booking_voucher_discount": "0.00",
        "weight": "0",
        "customer_prepaid": "0.00",
    }
    for decimal_field in ("discount_amount", "booking_voucher_discount", "weight", "customer_prepaid"):
        value = payload.get(decimal_field)
        if value in ("", None):
            payload[decimal_field] = decimal_defaults[decimal_field]
            continue
        try:
            payload[decimal_field] = str(_parse_decimal(value).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError, TypeError):
            payload[decimal_field] = decimal_defaults[decimal_field]

    customer_gender = payload.get("customer_gender")
    if customer_gender not in ("", None):
        normalized_gender = _normalize_gender_or_original(customer_gender)
        if normalized_gender is None:
            payload.pop("customer_gender", None)
        else:
            payload["customer_gender"] = normalized_gender
    else:
        payload.pop("customer_gender", None)

    order_type = str(payload.get("order_type") or "").strip()
    if order_type:
        payload["order_type"] = _normalize_order_type_or_original(order_type)
    else:
        payload.pop("order_type", None)

    payload["dimensions"] = _normalize_dimensions(payload.get("dimensions"))
    items, total_amount = _build_items_payload(
        sku_value=payload.get("sku_list"),
        quantity_value=payload.get("quantity_list"),
        price_value=payload.get("price_list"),
    )
    logger.debug(
        "Import row %s items parsed: item_count=%s total_amount=%s",
        row_number if row_number is not None else "?",
        len(items),
        str(total_amount.quantize(Decimal("0.01"))),
    )
    payload.pop("sku_list", None)
    payload.pop("quantity_list", None)
    payload.pop("price_list", None)
    payload["items"] = items
    payload["quantity"] = sum(item["quantity"] for item in items)
    payload["sku"] = ",".join(item["sku"] for item in items)
    payload["total_amount"] = _to_decimal_string_or_original(total_amount)
    discount_amount = _to_decimal_or_zero(payload.get("discount_amount"))
    booking_voucher_discount = _to_decimal_or_zero(payload.get("booking_voucher_discount"))
    total_after_discount = total_amount - discount_amount - booking_voucher_discount
    payload["total_after_discount"] = _to_decimal_string_or_original(total_after_discount)
    if payload.get("order_type") == OrderType.HAS_RETURN:
        prepaid = total_after_discount if total_after_discount > 0 else total_amount
        payload["customer_prepaid"] = _to_decimal_string_or_original(prepaid)
    logger.debug(
        "Import row %s totals: total_amount=%s discount=%s booking_voucher_discount=%s total_after_discount=%s",
        row_number if row_number is not None else "?",
        payload.get("total_amount"),
        payload.get("discount_amount"),
        payload.get("booking_voucher_discount"),
        payload.get("total_after_discount"),
    )
    # Force address normalization path in OrderService.create_order_manual:
    # raw_address -> parser -> catalog mapping -> ward/district/province/codes.
    payload["address"] = ""
    payload.pop("ward", None)
    payload.pop("district", None)
    payload.pop("province", None)
    payload.pop("ward_code", None)
    payload.pop("district_code", None)
    payload.pop("province_code", None)

    return payload


def _build_header_index(headers: list[str]) -> dict[str, int]:
    index_map: dict[str, int] = {}
    for index, raw_header in enumerate(headers):
        normalized_header = _normalize_header(raw_header)
        field_name = NORMALIZED_HEADER_ALIASES.get(normalized_header)
        if not field_name:
            continue
        # Keep first occurrence to avoid accidental duplicate-column overwrite.
        if field_name not in index_map:
            index_map[field_name] = index
    return index_map


def _normalize_header(header: Any) -> str:
    return _normalize_key(header)


def _normalize_cell_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _to_int_or_original(value: Any) -> Any:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return value


def _to_decimal_string_or_original(value: Any) -> Any:
    try:
        normalized = _parse_decimal(value)
    except (TypeError, InvalidOperation, ValueError):
        return value
    return str(normalized.quantize(Decimal("0.01")))


def _normalize_gender_or_original(value: Any) -> Any:
    if isinstance(value, bool):
        return 1 if value else 0
    normalized = _normalize_key(value)
    gender_aliases = {
        "true": 1,
        "false": 0,
        "0": 0,
        "female": 0,
        "nu": 0,
        "nữ": 0,
        "1": 1,
        "male": 1,
        "nam": 1,
    }
    return gender_aliases.get(normalized)


def _normalize_order_type_or_original(value: str) -> str:
    normalized = _normalize_key(value)
    order_type_aliases = {
        OrderType.NORMAL: OrderType.NORMAL,
        "normal": OrderType.NORMAL,
        "đơn thường": OrderType.NORMAL,
        "don thuong": OrderType.NORMAL,
        "thuong": OrderType.NORMAL,
        "thường": OrderType.NORMAL,
        OrderType.NO_RETURN: OrderType.NO_RETURN,
        "no_return": OrderType.NO_RETURN,
        "khong_hoan": OrderType.NO_RETURN,
        "không_hoàn": OrderType.NO_RETURN,
        "khong hoan": OrderType.NO_RETURN,
        "không hoàn": OrderType.NO_RETURN,
        "don khong tra": OrderType.NO_RETURN,
        "đơn không trả": OrderType.NO_RETURN,
        "khong tra": OrderType.NO_RETURN,
        "không trả": OrderType.NO_RETURN,
        OrderType.HAS_RETURN: OrderType.HAS_RETURN,
        "has_return": OrderType.HAS_RETURN,
        "co_hoan": OrderType.HAS_RETURN,
        "có_hoàn": OrderType.HAS_RETURN,
        "co hoan": OrderType.HAS_RETURN,
        "có hoàn": OrderType.HAS_RETURN,
        "don co tra": OrderType.HAS_RETURN,
        "đơn có trả": OrderType.HAS_RETURN,
        "co tra": OrderType.HAS_RETURN,
        "có trả": OrderType.HAS_RETURN,
    }
    return order_type_aliases.get(normalized, OrderType.NORMAL)


def _normalize_channel_code_or_original(value: Any) -> Any:
    normalized = _normalize_key(value)
    normalized = normalized.replace(".", " ")
    normalized = normalized.replace("_", " ")
    normalized = normalized.replace(" /", "/").replace("/ ", "/")
    normalized = " ".join(normalized.split())
    if not normalized:
        return value
    if normalized in NORMALIZED_CHANNEL_CODE_MAP:
        return NORMALIZED_CHANNEL_CODE_MAP[normalized]
    if normalized in {"facebook-cuong", "facebook-loi", "facebook", "web", "google-ads", "koc", "google-ads-web", "facebook-lan", "facebook-huy", "facebook-thanh", "facebook-an", "facebook-duc", "facebook-khanh"}:
        return normalized
    slugified = slugify(str(value).strip())
    return slugified or value


def _normalize_kiot_channel_id_or_original(value: Any) -> Any:
    normalized = _normalize_key(value)
    normalized = normalized.replace(".", " ").replace("-", " ").replace("/", " ")
    normalized = " ".join(normalized.split())
    if normalized in NORMALIZED_KIOT_CHANNEL_ID_MAP:
        return NORMALIZED_KIOT_CHANNEL_ID_MAP[normalized]

    compact = normalized.replace(" ", "_")
    if compact in NORMALIZED_KIOT_CHANNEL_ID_MAP:
        return NORMALIZED_KIOT_CHANNEL_ID_MAP[compact]

    # Handle combined labels like "new_kiot - old_kiot" by picking the first known token.
    token_candidates = ["new_kiot", "old_kiot", "hkd_kiot", "new kiot", "old kiot", "hkd kiot"]
    first_match = None
    first_pos = None
    for token in token_candidates:
        pos = normalized.find(token)
        if pos == -1:
            continue
        if first_pos is None or pos < first_pos:
            first_pos = pos
            first_match = token
    if first_match is not None:
        mapped = NORMALIZED_KIOT_CHANNEL_ID_MAP.get(first_match) or NORMALIZED_KIOT_CHANNEL_ID_MAP.get(
            first_match.replace(" ", "_")
        )
        if mapped is not None:
            return mapped

    parsed = _to_int_or_original(value)
    return parsed if isinstance(parsed, int) else None


def _normalize_dimensions(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for separator in ("x", "X", "*", "/"):
        text = text.replace(separator, ",")
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) == 3:
        return "x".join(parts)
    return text


def _split_multi_values(value: Any) -> list[str]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    if isinstance(value, bool):
        return Decimal("1" if value else "0")
    text = str(value).strip().replace(" ", "")
    if not text:
        return Decimal("0")

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")

    return Decimal(text)


def _to_decimal_or_zero(value: Any) -> Decimal:
    try:
        return _parse_decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _build_items_payload(*, sku_value: Any, quantity_value: Any, price_value: Any) -> tuple[list[dict[str, Any]], Decimal]:
    skus = _split_multi_values(sku_value)
    quantities_raw = _split_multi_values(quantity_value)
    prices_raw = _split_multi_values(price_value)

    if not skus:
        return [], Decimal("0")

    if quantities_raw and len(quantities_raw) != len(skus):
        if len(quantities_raw) == 1:
            quantities_raw = quantities_raw * len(skus)
        else:
            raise ValueError("Số lượng SL phải tương ứng với số lượng Mã SP.")
    if prices_raw and len(prices_raw) != len(skus):
        if len(prices_raw) == 1:
            prices_raw = prices_raw * len(skus)
        else:
            raise ValueError("Số lượng Giá phải tương ứng với số lượng Mã SP.")

    if not quantities_raw:
        quantities_raw = ["1"] * len(skus)
    if not prices_raw:
        prices_raw = ["0"] * len(skus)

    items: list[dict[str, Any]] = []
    total_amount = Decimal("0")
    for idx, sku in enumerate(skus):
        quantity = _to_int_or_original(quantities_raw[idx])
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError(f"SL không hợp lệ tại sản phẩm '{sku}'.")
        unit_price_decimal = _parse_decimal(prices_raw[idx])
        line_total = unit_price_decimal * Decimal(quantity)
        total_amount += line_total
        items.append(
            {
                "sku": sku,
                "quantity": quantity,
                "unit_price": str(unit_price_decimal.quantize(Decimal("0.01"))),
            }
        )
    return items, total_amount


def _ensure_products_exist(items: list[dict[str, Any]], row_number: int | None = None) -> None:
    for item in items:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            continue
        product = Product.objects.filter(sku__iexact=sku).first()
        unit_price = _to_decimal_or_zero(item.get("unit_price"))
        if product is None:
            Product.objects.create(
                name=sku,
                sku=sku,
                price=unit_price,
                is_active=True,
            )
            logger.debug(
                "Import row %s product auto-created: sku=%s price=%s",
                row_number if row_number is not None else "?",
                sku,
                str(unit_price.quantize(Decimal("0.01"))),
            )
            continue
        update_fields: list[str] = []
        if not product.is_active:
            if is_trash_sku(product.sku):
                # Soft-deleted trash/old SKU (see migration 0079): keep it
                # deleted instead of resurrecting it into the active catalog.
                # The order item still links to this existing product.
                logger.warning(
                    "Import row %s skipped resurrecting trash SKU=%s",
                    row_number if row_number is not None else "?",
                    product.sku,
                )
            else:
                product.is_active = True
                product.deleted_at = None
                update_fields.extend(["is_active", "deleted_at"])
        if update_fields:
            product.save(update_fields=[*update_fields, "updated_at"])
            logger.debug(
                "Import row %s product updated: sku=%s fields=%s",
                row_number if row_number is not None else "?",
                sku,
                ",".join(update_fields),
            )


def import_orders_from_text(
    *,
    text_data: str,
    actor,
    extra_payload: dict[str, Any] | None = None,
    field_layout: list[str] | None = None,
) -> dict[str, Any]:
    """Import orders from pipe-delimited text format.

    Expected format: Each line represents one order with fields separated by |
    Fields order: kiotviet_channel_id|staff_username|channel_code|name|customer_gender|phone|raw_address|sku_list|quantity_list|price_list|discount_amount|booking_voucher_discount|weight|dimensions|branch_id|order_type|customer_prepaid

    `extra_payload` (optional) is merged into every row payload — see
    `import_orders_from_workbook` for the landing-orders use case.

    `field_layout` (optional) overrides the positional column layout. Defaults to
    `IMPORT_PAYLOAD_FIELDS` (17-col). The KOC import passes
    `KOC_IMPORT_PAYLOAD_FIELDS` (19-col) to map its own column order.
    """
    layout = field_layout or IMPORT_PAYLOAD_FIELDS
    header_index = {field: idx for idx, field in enumerate(layout)}
    lines = [line.strip() for line in text_data.strip().split('\n') if line.strip()]
    if not lines:
        return {
            "total_rows": 0,
            "success_count": 0,
            "failed_count": 0,
            "failures": [],
            "warnings": [],
        }

    failures: list[ImportedOrderFailure] = []
    success_count = 0
    total_rows = 0
    staff_column_ignored = False

    for row_number, line in enumerate(lines, start=1):
        try:
            row = [field.strip() for field in line.split('|')]
            if len(row) < len(layout):
                # Pad with empty strings if not enough fields
                row.extend([""] * (len(layout) - len(row)))

            row_payload = _build_row_payload(tuple(row), header_index, row_number=row_number)
            if extra_payload:
                row_payload.update(extra_payload)
        except Exception as exc:
            logger.debug("Import row %s payload build failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Dữ liệu dòng không hợp lệ.",
                )
            )
            continue
        total_rows += 1
        logger.debug(
            "Import row %s payload parsed: phone=%s channel_code=%s kiotviet_channel_id=%s order_type=%s items=%s discount=%s booking_voucher_discount=%s",
            row_number,
            row_payload.get("phone"),
            row_payload.get("channel_code"),
            row_payload.get("kiotviet_channel_id"),
            row_payload.get("order_type"),
            len(row_payload.get("items") or []),
            row_payload.get("discount_amount"),
            row_payload.get("booking_voucher_discount"),
        )
        try:
            _ensure_products_exist(row_payload.get("items") or [], row_number=row_number)
        except Exception as exc:
            logger.debug("Import row %s ensure products failed: %s", row_number, exc, exc_info=True)
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error=str(exc) or "Không thể tạo sản phẩm mới từ dữ liệu dòng này.",
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        serializer = OrderCreateSerializer(data=row_payload)
        if not serializer.is_valid():
            logger.debug(
                "Import row %s serializer invalid: errors=%s payload_keys=%s",
                row_number,
                serializer.errors,
                sorted(row_payload.keys()),
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
        channel = validated["channel"]
        if not OrderService.can_edit_channel(actor, channel.code):
            logger.debug(
                "Import row %s permission denied for actor=%s channel=%s",
                row_number,
                getattr(actor, "id", None),
                channel.code,
            )
            failures.append(
                ImportedOrderFailure(
                    row_number=row_number,
                    error="Bạn không có quyền tạo đơn cho kênh này.",
                    phone=str(row_payload.get("phone") or ""),
                    name=str(row_payload.get("name") or ""),
                )
            )
            continue

        if resolve_import_staff(validated, actor):
            staff_column_ignored = True

        try:
            OrderService.create_order_manual(validated, actor, created_via="excel_import")
        except Exception as exc:
            logger.debug(
                "Import row %s create_order_manual failed: %s",
                row_number,
                exc,
                exc_info=True,
            )
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
        logger.debug(
            "Import row %s success: phone=%s channel=%s",
            row_number,
            row_payload.get("phone"),
            channel.code,
        )

    return {
        "total_rows": total_rows,
        "success_count": success_count,
        "failed_count": len(failures),
        "failures": [failure.to_payload() for failure in failures],
        "warnings": [STAFF_COLUMN_IGNORED_WARNING] if staff_column_ignored else [],
    }


def import_orders_from_koc_text(
    *,
    text_data: str,
    actor,
) -> dict[str, Any]:
    """Import đơn KOC từ text dán theo layout 19 cột (KOC_IMPORT_PAYLOAD_FIELDS).

    Fields order: kiotviet_channel_id|customer_code|staff_username|channel_code|name|customer_gender|phone|raw_address|<khu vực>|<phường/xã>|sku_list|quantity_list|price_list|booking_voucher_discount|discount_amount|weight|dimensions|branch_id|order_type
    """
    return import_orders_from_text(
        text_data=text_data,
        actor=actor,
        field_layout=KOC_IMPORT_PAYLOAD_FIELDS,
    )


def _flatten_serializer_errors(errors: Any) -> str:
    if isinstance(errors, dict):
        parts = []
        for key, value in errors.items():
            parts.append(f"{key}: {_flatten_serializer_errors(value)}")
        return "; ".join(parts)
    if isinstance(errors, list):
        return "; ".join(_flatten_serializer_errors(item) for item in errors)
    return str(errors)
