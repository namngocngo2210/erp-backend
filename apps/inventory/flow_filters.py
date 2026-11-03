"""Parse tham số bộ lọc của Lịch sử nhập xuất — dùng chung cho lớp transport
(`transport/views/flow_history_views.py`, `awaiting_return_views.py`) và cho job
export nền (`exports_definitions.py`).

Hai nơi đó đọc từ nguồn khác nhau — query string (luôn là chuỗi) và JSON params
do FE gửi khi tạo job (có thể là số/mảng) — nên helper ở đây nhận CẢ HAI dạng.
Trước khi có module này, luật parse bị chép làm hai bản và đã một lần lệch nhau
(bộ lọc bị bỏ qua âm thầm trong file Excel trong khi màn hình vẫn lọc đúng).
"""
from __future__ import annotations

from collections.abc import Iterable


def int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def int_csv(value) -> list[int]:
    """ "1,5,9" → [1, 5, 9]; phần tử không phải số bị bỏ qua.

    Nhận cả list/tuple (params JSON của job export) — ``str([1, 2])`` mà đem
    split "," sẽ ra rác và bộ lọc bị bỏ qua KHÔNG một tiếng động, nên xử lý
    thẳng ở đây thay vì tin nguồn gọi luôn gửi chuỗi.
    """
    if isinstance(value, (list, tuple)):
        raw_parts: Iterable = value
    else:
        raw_parts = str(value or "").split(",")
    return [
        num
        for raw in raw_parts
        if (num := int_or_none(str(raw).strip())) is not None
    ]


def parse_warehouse_ids(raw_many, raw_single) -> list[int]:
    """Bộ lọc kho: đọc danh sách ``warehouse_ids`` ("1,5"), vẫn nhận
    ``warehouse_id`` đơn lẻ của bản cũ (link đã lưu, tích hợp ngoài).

    Danh sách RỖNG = không lọc theo kho (tất cả kho), đúng ngữ nghĩa
    ``warehouse_id=None`` trước đây. Id trùng bị gộp nhưng giữ nguyên thứ tự để
    queryKey/log ổn định giữa các lần gọi.
    """
    ids = int_csv(raw_many)
    single = int_or_none(raw_single)
    if single is not None:
        ids.append(single)
    return list(dict.fromkeys(i for i in ids if i))
