"""Đọc/ghi file Excel danh sách nội dung Media (import hàng loạt + xuất báo cáo).

Thuần openpyxl: KHÔNG gọi mạng, KHÔNG biết ads-crm. Tầng service lo phần đối
chiếu tên taxonomy → id và gọi API. Cột file mẫu do chủ dự án chốt:

    uploaded_at | source_name | title | url | content_type | products | categories

File xuất giữ nguyên 7 cột đó ở đầu (để sửa xong import ngược lại được) rồi mới
tới các cột chỉ-đọc (mã, rating, trạng thái video) — parser bỏ qua cột lạ.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook

from apps.common.excel_utils import append_clean_row

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

IMPORT_HEADERS: tuple[str, ...] = (
    "uploaded_at", "source_name", "title", "url", "content_type", "products", "categories",
)
EXPORT_EXTRA_HEADERS: tuple[str, ...] = ("content_code", "overall_rating", "video_status")
SAMPLE_ROW: tuple[str, ...] = (
    "2026-09-10", "Youtube adminkhang", "Tieu de noi dung mau",
    "https://example.com/video", "Video Ads Booking", "Thảm TMS01", "Máy massage chân",
)
#: Tách nhiều giá trị trong 1 ô (products/categories). Tên có dấu phẩy thì dùng "|" hoặc xuống dòng.
MULTI_SEPARATORS = re.compile(r"[,;|\n]")

# Tên cột chấp nhận được ngoài khoá tiếng Anh ở IMPORT_HEADERS (người dùng hay
# đổi header sang tiếng Việt cho dễ đọc rồi import lại).
_HEADER_ALIASES: dict[str, str] = {
    "ngay upload": "uploaded_at", "ngay dang": "uploaded_at", "ngay": "uploaded_at",
    "nguon": "source_name", "ten nguon": "source_name",
    "tieu de": "title", "ten noi dung": "title",
    "link": "url", "duong dan": "url",
    "loai noi dung": "content_type", "loai": "content_type",
    "san pham": "products", "ma san pham": "products",
    "danh muc": "categories", "nhom san pham": "categories",
}


def strip_accents(value: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp khoan dung (đ/Đ xử lý riêng vì không tách được)."""
    text = value.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def normalize_key(value: Any) -> str:
    """Chuẩn hoá chuỗi để so khớp: bỏ khoảng trắng thừa + hạ chữ thường."""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip().casefold()


def _header_field(cell: Any) -> str | None:
    key = normalize_key(cell)
    if key in IMPORT_HEADERS:
        return key
    return _HEADER_ALIASES.get(strip_accents(key))


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def split_multi(value: str) -> list[str]:
    return [part.strip() for part in MULTI_SEPARATORS.split(value) if part.strip()]


@dataclass(slots=True)
class ImportRow:
    """Một dòng thô đã đọc từ file, chưa đối chiếu taxonomy."""

    row_number: int
    uploaded_at: str = ""
    source_name: str = ""
    title: str = ""
    url: str = ""
    content_type: str = ""
    products: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.uploaded_at, self.source_name, self.title, self.url, self.content_type,
             self.products, self.categories)
        )


class ExcelFormatError(ValueError):
    """File không đọc được hoặc thiếu cột bắt buộc."""


def parse_import_rows(file_obj: Any, *, max_rows: int) -> list[ImportRow]:
    """Đọc sheet đầu tiên thành list ImportRow (bỏ dòng trống hoàn toàn).

    Raise ExcelFormatError khi file hỏng, không có header, thiếu cột bắt buộc,
    hoặc vượt quá `max_rows` dòng dữ liệu.
    """
    try:
        workbook = load_workbook(filename=file_obj, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl ném đủ loại lỗi cho file hỏng/không phải xlsx
        raise ExcelFormatError("Không đọc được file Excel. Cần file .xlsx theo mẫu.") from exc
    try:
        worksheet = workbook.active
        rows_iter = worksheet.iter_rows(values_only=True)
        header = next(rows_iter, None)
        if not header:
            raise ExcelFormatError("File Excel rỗng.")
        columns = {}
        for index, cell in enumerate(header):
            name = _header_field(cell)
            if name and name not in columns:
                columns[name] = index
        missing = [c for c in ("uploaded_at", "source_name", "title", "url") if c not in columns]
        if missing:
            raise ExcelFormatError(
                "File thiếu cột bắt buộc: " + ", ".join(missing) + ". Hãy tải lại file mẫu."
            )

        rows: list[ImportRow] = []
        for offset, values in enumerate(rows_iter, start=2):
            def col(name: str) -> str:
                index = columns.get(name)
                return _cell_text(values[index]) if index is not None and index < len(values) else ""

            row = ImportRow(
                row_number=offset,
                uploaded_at=col("uploaded_at"),
                source_name=col("source_name"),
                title=col("title"),
                url=col("url"),
                content_type=col("content_type"),
                products=split_multi(col("products")),
                categories=split_multi(col("categories")),
            )
            if row.is_empty:
                continue
            rows.append(row)
            if len(rows) > max_rows:
                raise ExcelFormatError(f"File quá {max_rows} dòng. Hãy chia nhỏ file rồi import lại.")
        return rows
    finally:
        workbook.close()


def _sheet(title: str) -> tuple[Workbook, Any]:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = title
    return workbook, worksheet


def _to_bytes(workbook: Workbook) -> bytes:
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _autosize(worksheet, widths: tuple[int, ...]) -> None:
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[worksheet.cell(row=1, column=index).column_letter].width = width


def build_import_template() -> bytes:
    """File mẫu: header + 1 dòng ví dụ + sheet hướng dẫn cách điền."""
    workbook, worksheet = _sheet("Noi dung")
    append_clean_row(worksheet, IMPORT_HEADERS)
    append_clean_row(worksheet, SAMPLE_ROW)
    _autosize(worksheet, (14, 22, 34, 34, 20, 24, 24))

    guide = workbook.create_sheet("Huong dan")
    for line in (
        ("Cột", "Bắt buộc", "Cách điền"),
        ("uploaded_at", "Có", "Ngày upload: 2026-09-10 hoặc 10/09/2026."),
        ("source_name", "Có", "Tên nguồn đúng như trong mục Danh mục → Nguồn nội dung."),
        ("title", "Có", "Tiêu đề nội dung."),
        ("url", "Có", "Link nội dung, bắt đầu bằng http:// hoặc https://."),
        ("content_type", "Không", "Tên loại nội dung; để trống nếu chưa phân loại."),
        ("products", "Không", "Tên hoặc mã sản phẩm; nhiều giá trị ngăn bằng dấu phẩy."),
        ("categories", "Không", "Tên danh mục; nhiều giá trị ngăn bằng dấu phẩy."),
        ("", "", ""),
        ("Lưu ý", "", "Tên nguồn/loại/sản phẩm/danh mục phải có sẵn trên hệ thống Media."),
        ("", "", "Dòng sai sẽ bị bỏ qua và báo lại; các dòng hợp lệ vẫn được tạo."),
    ):
        append_clean_row(guide, line)
    _autosize(guide, (18, 10, 70))
    return _to_bytes(workbook)


def _names(items: Any) -> str:
    if not items:
        return ""
    return ", ".join(str(item.get("name") or "") for item in items if isinstance(item, dict))


def build_contents_export(contents: list[dict[str, Any]]) -> bytes:
    """Xuất danh sách nội dung ra xlsx (7 cột import + 3 cột chỉ đọc)."""
    workbook, worksheet = _sheet("Noi dung")
    append_clean_row(worksheet, IMPORT_HEADERS + EXPORT_EXTRA_HEADERS)
    for content in contents:
        source = content.get("source") or {}
        content_type = content.get("content_type") or {}
        append_clean_row(
            worksheet,
            (
                _cell_text(content.get("uploaded_at"))[:10],
                source.get("name") or "",
                content.get("title") or "",
                content.get("url") or "",
                content_type.get("name") or "",
                _names(content.get("products")),
                _names(content.get("categories")),
                content.get("content_code") or "",
                content.get("overall_rating") or "",
                content.get("video_status") or "",
            ),
        )
    _autosize(worksheet, (14, 22, 34, 34, 20, 24, 24, 14, 14, 14))
    return _to_bytes(workbook)
