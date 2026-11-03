from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MartTable:
    """Mô tả một bảng mart: transform là hàm sinh SELECT, loader lo phần còn lại.

    select_sql nhận mệnh đề WHERE (không kèm từ khoá WHERE) — full_rebuild truyền
    scope mặc định, upsert truyền scope + lọc id. Nhờ đó CHỈ CÓ MỘT câu SELECT
    cho cả hai đường nạp: hết loại bug "hai nhánh code lệch nhau".
    """

    name: str
    key_column: str
    group: str  # "hot" | "nightly"
    select_sql: Callable[[str], str]
    candidate_ids_sql: str | None = None
    source_key: str = "o.id"
