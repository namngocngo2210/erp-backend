from __future__ import annotations

import logging

from django.db import connection, transaction

logger = logging.getLogger(__name__)

# DEFAULT partition name created by migration inventory.0008.
_DEFAULT_PARTITION = "stock_movements_default"


class PartitionService:
    """Quản lý partition theo tháng cho bảng ledger stock_movements."""

    @staticmethod
    def _month_bounds(year: int, month: int) -> tuple[str, str, str]:
        name = f"stock_movements_{year:04d}{month:02d}"
        start = f"{year:04d}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1:04d}-01-01"
        else:
            end = f"{year:04d}-{month + 1:02d}-01"
        return name, start, end

    @staticmethod
    @transaction.atomic
    def ensure_month_partition(year: int, month: int) -> str:
        """Tạo partition cho (year, month) nếu chưa có. Idempotent VÀ tự lành.

        Nếu DEFAULT partition đã ôm dữ liệu của tháng này (do beat lỡ nhịp), một
        lệnh CREATE ... PARTITION OF thẳng sẽ FAIL vì overlap. Thay vào đó: tách
        DEFAULT ra, tạo partition, dời dữ liệu tháng đó sang, xoá khỏi DEFAULT, rồi
        gắn DEFAULT lại — tất cả trong 1 transaction. Trả tên partition.
        """
        name, start, end = PartitionService._month_bounds(year, month)
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass(%s);", [f"public.{name}"])
            if cur.fetchone()[0] is not None:
                return name

            cur.execute(
                f"SELECT EXISTS(SELECT 1 FROM {_DEFAULT_PARTITION} "
                "WHERE created_at >= %s AND created_at < %s);",
                [start, end],
            )
            default_has_rows = cur.fetchone()[0]

            if default_has_rows:
                logger.warning(
                    "stock_movements_default chứa dữ liệu tháng %s-%s; tách DEFAULT để tạo partition %s",
                    year, month, name,
                )
                cur.execute(
                    f"ALTER TABLE stock_movements DETACH PARTITION {_DEFAULT_PARTITION};"
                )
                cur.execute(
                    f"CREATE TABLE {name} PARTITION OF stock_movements "
                    "FOR VALUES FROM (%s) TO (%s);",
                    [start, end],
                )
                cur.execute(
                    f"INSERT INTO {name} SELECT * FROM {_DEFAULT_PARTITION} "
                    "WHERE created_at >= %s AND created_at < %s;",
                    [start, end],
                )
                cur.execute(
                    f"DELETE FROM {_DEFAULT_PARTITION} "
                    "WHERE created_at >= %s AND created_at < %s;",
                    [start, end],
                )
                cur.execute(
                    f"ALTER TABLE stock_movements ATTACH PARTITION {_DEFAULT_PARTITION} DEFAULT;"
                )
            else:
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF stock_movements "
                    "FOR VALUES FROM (%s) TO (%s);",
                    [start, end],
                )
        return name
