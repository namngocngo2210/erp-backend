"""Import/export nội dung Media bằng file Excel.

ads-crm KHÔNG có API tạo hàng loạt (chỉ `POST /contents` từng bản ghi và
`GET /contents/export` ra CSV), nên desi-order tự đọc file rồi gọi lần lượt qua
`MediaProxy` — mỗi dòng vẫn đi qua đúng lớp phân quyền và ghi bảng owner như khi
tạo tay trên UI.

Quy ước: dòng lỗi bị bỏ qua và báo lại theo số dòng; các dòng hợp lệ vẫn được tạo
(ads-crm không có transaction, dừng giữa chừng cũng không rollback được phần đã tạo).
Lỗi hạ tầng (mất kết nối, sai key, hết quyền) thì dừng cả file — mọi dòng sau sẽ
hỏng y hệt, chạy tiếp chỉ tốn thời gian và sinh 500 dòng lỗi giống nhau.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from apps.media.domain.roles import Action
from apps.media.exceptions import AdsCrmApiError
from apps.media.infrastructure.content_excel import (
    ImportRow,
    normalize_key,
    parse_import_rows,
    strip_accents,
)

MAX_IMPORT_ROWS = 500
MAX_EXPORT_ROWS = 5000
EXPORT_PAGE_SIZE = 200
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")
_EXCEL_EPOCH = date(1899, 12, 30)  # Excel đếm ngày từ đây (đã tính cả bug năm 1900)


class RowError(ValueError):
    """Lỗi của một dòng — không làm hỏng cả file."""


@dataclass(slots=True)
class TaxonomyIndex:
    """Tra id taxonomy theo tên/mã người dùng gõ trong file.

    Khớp lần lượt: tên chuẩn hoá → bỏ dấu. Nhận cả `code`, `name` và dạng ghép
    "MÃ – Tên" (đúng cách MultiSelect trên UI hiển thị, người dùng hay copy y nguyên).
    """

    by_key: dict[str, int]
    by_ascii: dict[str, int]

    @classmethod
    def build(cls, items: Any) -> TaxonomyIndex:
        by_key: dict[str, int] = {}
        by_ascii: dict[str, int] = {}
        for item in items or []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            name, code = str(item.get("name") or ""), str(item.get("code") or "")
            labels = [name, code, f"{code} – {name}", f"{code} - {name}"] if code else [name]
            for label in labels:
                key = normalize_key(label)
                if key:
                    by_key.setdefault(key, item["id"])
                    by_ascii.setdefault(normalize_key(strip_accents(key)), item["id"])
        return cls(by_key=by_key, by_ascii=by_ascii)

    def get(self, label: str) -> int | None:
        key = normalize_key(label)
        return self.by_key.get(key) or self.by_ascii.get(normalize_key(strip_accents(key)))

    def resolve_many(self, labels: list[str], *, what: str) -> list[int]:
        ids, unknown = [], []
        for label in labels:
            found = self.get(label)
            if found is None:
                unknown.append(label)
            elif found not in ids:
                ids.append(found)
        if unknown:
            raise RowError(f"Không tìm thấy {what}: " + ", ".join(unknown))
        return ids


def parse_uploaded_at(raw: str) -> str:
    """Chuẩn hoá ngày upload về YYYY-MM-DD (ads-crm chỉ nhận dạng này)."""
    text = (raw or "").strip()
    if not text:
        raise RowError("Thiếu ngày upload.")
    if text.isdigit() and len(text) <= 6:  # ô định dạng General → Excel trả số seri
        return (_EXCEL_EPOCH + timedelta(days=int(text))).isoformat()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    raise RowError(f"Ngày upload không hợp lệ: {raw!r}. Dùng dạng 2026-09-10 hoặc 10/09/2026.")


def build_payload(row: ImportRow, taxonomy: dict[str, TaxonomyIndex]) -> dict[str, Any]:
    """Đổi một dòng file thành body `POST /contents`. Raise RowError nếu dòng sai."""
    uploaded_at = parse_uploaded_at(row.uploaded_at)
    title = row.title.strip()
    if not title:
        raise RowError("Thiếu tiêu đề.")
    url = row.url.strip()
    if not url.startswith(("http://", "https://")) or len(url.split("://", 1)[1].split("/")[0]) < 3:
        raise RowError(f"URL không hợp lệ: {row.url!r}. Cần bắt đầu bằng http:// hoặc https://.")
    if not row.source_name.strip():
        raise RowError("Thiếu nguồn.")
    source_id = taxonomy["content-sources"].get(row.source_name)
    if source_id is None:
        raise RowError(f"Không tìm thấy nguồn: {row.source_name}")
    content_type_id = None
    if row.content_type.strip():
        content_type_id = taxonomy["content-types"].get(row.content_type)
        if content_type_id is None:
            raise RowError(f"Không tìm thấy loại nội dung: {row.content_type}")
    return {
        "uploaded_at": uploaded_at,
        "title": title,
        "url": url,
        "source_id": source_id,
        "content_type_id": content_type_id,
        "product_ids": taxonomy["products"].resolve_many(row.products, what="sản phẩm"),
        "category_ids": taxonomy["product-categories"].resolve_many(row.categories, what="danh mục"),
    }


def _taxonomy_indexes(proxy) -> dict[str, TaxonomyIndex]:
    indexes = {}
    for group in ("content-sources", "content-types", "products", "product-categories"):
        data = proxy.taxonomy(group, False)
        items = data.get("items") if isinstance(data, dict) else data
        indexes[group] = TaxonomyIndex.build(items)
    return indexes


def import_contents(proxy, file_obj) -> dict[str, Any]:
    """Đọc file → tạo từng nội dung qua ads-crm. Trả tổng kết + danh sách dòng lỗi."""
    rows = parse_import_rows(file_obj, max_rows=MAX_IMPORT_ROWS)
    result: dict[str, Any] = {"total_rows": len(rows), "created": 0, "created_ids": [], "errors": []}
    if not rows:
        return result

    taxonomy = _taxonomy_indexes(proxy)
    for row in rows:
        try:
            payload = build_payload(row, taxonomy)
        except RowError as exc:
            result["errors"].append({"row": row.row_number, "title": row.title, "message": str(exc)})
            continue
        try:
            _, data = proxy.forward("POST", "/contents", action=Action.CONTENT_CREATE, json=payload)
        except AdsCrmApiError as exc:
            # Lỗi nghiệp vụ của riêng dòng này (400/409/422…) — ghi nhận rồi chạy tiếp.
            if exc.status >= 500:
                raise
            result["errors"].append({"row": row.row_number, "title": row.title, "message": exc.detail})
            continue
        remote_id = data.get("id") if isinstance(data, dict) else None
        if isinstance(remote_id, int):
            proxy.record_content_owner(remote_id)
            result["created_ids"].append(remote_id)
        result["created"] += 1
    return result


def fetch_contents_for_export(proxy, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Lấy toàn bộ nội dung khớp bộ lọc, phân trang tới khi hết hoặc chạm trần."""
    base = [(key, value) for key, value in params if key not in ("page", "page_size")]
    items: list[dict[str, Any]] = []
    page = 1
    while len(items) < MAX_EXPORT_ROWS:
        query = [*base, ("page", str(page)), ("page_size", str(EXPORT_PAGE_SIZE))]
        _, data = proxy.forward("GET", "/contents", action=Action.READ, params=query)
        batch = (data.get("items") if isinstance(data, dict) else None) or []
        items.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < EXPORT_PAGE_SIZE:
            break
        page += 1
    return items[:MAX_EXPORT_ROWS]


__all__ = [
    "MAX_EXPORT_ROWS",
    "MAX_IMPORT_ROWS",
    "RowError",
    "TaxonomyIndex",
    "build_payload",
    "fetch_contents_for_export",
    "import_contents",
    "parse_uploaded_at",
]
