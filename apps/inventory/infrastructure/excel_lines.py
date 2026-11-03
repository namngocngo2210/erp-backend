from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook

from apps.catalog.models import Product

logger = logging.getLogger(__name__)

# ── Column aliases ────────────────────────────────────────────────────────────

_SKU_ALIASES: frozenset[str] = frozenset(
    {"sku", "mã sp", "ma sp", "mã sản phẩm", "ma san pham"}
)
_QUANTITY_ALIASES: frozenset[str] = frozenset(
    {"số lượng", "so luong", "sl", "quantity", "qty"}
)
_NOTE_ALIASES: frozenset[str] = frozenset(
    {"ghi chú", "ghi chu", "note", "condition"}
)


def _normalize_key(value: Any) -> str:
    """Lowercase + strip for case-insensitive header matching."""
    return str(value or "").strip().lower()


def _parse_qty(value: Any) -> int:
    """Parse a cell value to a positive integer, raise ValueError on failure."""
    if value is None:
        raise ValueError("Số lượng trống.")
    try:
        qty = int(float(str(value).strip()))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Số lượng không hợp lệ: {value!r}") from exc
    if qty <= 0:
        raise ValueError(f"Số lượng phải > 0, nhận được: {qty}")
    return qty


def _build_col_index(header_row: tuple[Any, ...]) -> dict[str, int]:
    """Return a mapping of {'sku'|'quantity'|'note': column_index}."""
    index: dict[str, int] = {}
    for col_idx, cell in enumerate(header_row):
        key = _normalize_key(cell)
        if "sku" not in index and key in _SKU_ALIASES:
            index["sku"] = col_idx
        elif "quantity" not in index and key in _QUANTITY_ALIASES:
            index["quantity"] = col_idx
        elif "note" not in index and key in _NOTE_ALIASES:
            index["note"] = col_idx
    return index


def parse_lines_from_workbook(file_obj: Any) -> dict[str, Any]:
    """Parse an uploaded .xlsx file into product lines.

    Args:
        file_obj: A file-like object (Django ``UploadedFile`` or ``BytesIO``).

    Returns:
        ``{"lines": [...], "errors": [...]}``
        Lines shape: {product_id, sku, name, quantity, condition_note}
        Errors shape: {row, sku, message}

    Raises:
        ValueError: If the file is missing the required SKU or Số lượng column.
    """
    wb = load_workbook(filename=file_obj, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        return {"lines": [], "errors": []}

    col_index = _build_col_index(header_row)

    if "sku" not in col_index or "quantity" not in col_index:
        raise ValueError("File thiếu cột SKU hoặc Số lượng.")

    sku_col = col_index["sku"]
    qty_col = col_index["quantity"]
    note_col = col_index.get("note")

    # product_id → accumulated line (for deduplication / merge)
    merged: dict[int, dict[str, Any]] = {}
    # rows that could not be parsed
    errors: list[dict[str, Any]] = []

    for row_number, row in enumerate(rows_iter, start=2):
        # Skip fully-empty rows
        if not any(str(cell or "").strip() for cell in row):
            continue

        raw_sku = str(row[sku_col] if sku_col < len(row) else "").strip()
        raw_note = ""
        if note_col is not None and note_col < len(row):
            raw_note = str(row[note_col] or "").strip()

        # Validate SKU
        if not raw_sku:
            errors.append({"row": row_number, "sku": "", "message": "SKU trống."})
            continue

        # Validate quantity
        raw_qty = row[qty_col] if qty_col < len(row) else None
        try:
            qty = _parse_qty(raw_qty)
        except ValueError as exc:
            errors.append({"row": row_number, "sku": raw_sku, "message": str(exc)})
            continue

        # Resolve product
        try:
            product = Product.objects.filter(sku=raw_sku, deleted_at__isnull=True).first()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Row %s DB lookup failed for sku=%s", row_number, raw_sku)
            errors.append(
                {"row": row_number, "sku": raw_sku, "message": f"Lỗi tra cứu SKU: {exc}"}
            )
            continue

        if product is None:
            errors.append(
                {"row": row_number, "sku": raw_sku, "message": "SKU không tồn tại."}
            )
            continue

        # Merge duplicate SKUs
        if product.id in merged:
            merged[product.id]["quantity"] += qty
            # Keep first non-empty note
            if not merged[product.id]["condition_note"] and raw_note:
                merged[product.id]["condition_note"] = raw_note
        else:
            merged[product.id] = {
                "product_id": product.id,
                "sku": product.sku,
                "name": product.name,
                "quantity": qty,
                "condition_note": raw_note,
            }

    lines = list(merged.values())
    return {"lines": lines, "errors": errors}


def build_lines_import_template() -> bytes:
    """Return bytes of a minimal .xlsx template for inventory line import."""
    wb = Workbook()
    ws = wb.active
    ws.title = "inventory_import"
    ws.append(["SKU", "Số lượng", "Ghi chú"])
    ws.append(["VD-SKU-001", 10, "ghi chú (tuỳ chọn)"])
    ws.freeze_panes = "A2"

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
