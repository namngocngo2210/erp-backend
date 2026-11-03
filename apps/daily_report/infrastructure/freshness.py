"""Độ tươi của mart ai.don_hang — đọc từ view ai.tinh_trang_du_lieu.

Đây là thuốc giải cho chế độ hỏng nguy hiểm nhất: ETL trễ âm thầm. Mọi response
kèm mốc này để trợ lý nói "số liệu tính đến 07:37" thay vì tưởng là số hiện tại.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError, connection

logger = logging.getLogger(__name__)

OK_STATUS = "Bình thường"
NO_DATA_STATUS = "Chưa có dữ liệu — mart chưa từng chạy"
ERROR_STATUS = "Lỗi đọc mart — DB hoặc view ai.tinh_trang_du_lieu không truy cập được"
MART_TABLE = "ai.don_hang"


@dataclass(frozen=True)
class Freshness:
    data_as_of: datetime | None
    lag_minutes: int | None
    status: str

    @property
    def ok(self) -> bool:
        return self.status == OK_STATUS

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data_as_of": self.data_as_of.isoformat() if self.data_as_of else None,
            "lag_minutes": self.lag_minutes,
            "status": self.status,
        }


def read_freshness() -> Freshness:
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT du_lieu_toi_moc, do_tre_phut, trang_thai_text"
                " FROM ai.tinh_trang_du_lieu WHERE ten_bang = %s",
                [MART_TABLE],
            )
            row = cur.fetchone()
    except DatabaseError:
        logger.exception("daily_report: đọc freshness thất bại")
        return Freshness(None, None, ERROR_STATUS)
    if row is None:
        return Freshness(None, None, NO_DATA_STATUS)
    return Freshness(row[0], row[1], row[2])
