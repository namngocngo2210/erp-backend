"""DTO thuần cho dữ liệu đọc từ Foxia — không phụ thuộc Django.

Tách khỏi model để tầng domain/mapper làm việc với dữ liệu đã chuẩn hoá thay vì
dict thô của JSON-RPC: chỗ duy nhất phải biết Foxia trả `[id, "tên"]` cho quan
hệ many2one là `many2one_id` / `many2one_name` ở đây.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def many2one_id(value: Any) -> int | None:
    """Lấy id từ giá trị many2one.

    Foxia (Odoo) trả quan hệ dưới dạng ``[id, "tên hiển thị"]``, hoặc ``False``
    khi rỗng. Một số field khai báo là selection lại trả thẳng chuỗi/số — nhận
    cả hai dạng để mapper không phải đoán.
    """
    if isinstance(value, (list, tuple)):
        return int(value[0]) if value and value[0] not in (None, False) else None
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def many2one_name(value: Any) -> str:
    """Lấy tên hiển thị từ giá trị many2one.

    CẢNH BÁO: tên hiển thị KHÔNG ổn định giữa các lần đồng bộ (tài liệu Foxia
    §4). Dùng để đọc và thống kê, không dùng làm khoá join — join phải bám id.
    """
    if isinstance(value, (list, tuple)):
        return str(value[1]).strip() if len(value) > 1 and value[1] not in (None, False) else ""
    if isinstance(value, bool) or value is None:
        return ""
    return str(value).strip()


@dataclass(frozen=True)
class CatalogRecordDTO:
    """Một bản ghi danh mục đã chuẩn hoá: id gốc + tên + các quan hệ kèm theo.

    `relations` giữ id của các many2one phụ thuộc (nguồn → kênh/thương hiệu/...)
    dưới dạng tên field nội bộ → id Foxia, để service gán FK mà không cần biết
    tên field bên Foxia.
    """

    foxia_id: int
    name: str = ""
    extra: dict = field(default_factory=dict)
    relations: dict = field(default_factory=dict)
