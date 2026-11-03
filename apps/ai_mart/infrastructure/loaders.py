"""Nạp dữ liệu vào schema ai. Target đi qua alias connection Django — đổi target
là đổi env, không phải viết lại transform (spec §2, giữ Q1/Q3 đảo ngược được)."""

import logging

from django.db import connections, transaction

from apps.ai_mart.infrastructure.sql.base import MartTable

logger = logging.getLogger(__name__)

TARGET_ALIAS = "default"

# work_mem cục bộ cho transaction ETL (audit 2026-09-23 C5/P1-3, P1-4). Server
# để 16MB: khach_hang/don_hang aggregate + sort 560k dòng tràn ra temp file
# (pg_stat_database.temp_files 843 / 7,7GB trong 20h đo prod 23/09). SET LOCAL
# chỉ sống trong transaction của loader — không đụng session khác, an toàn cả
# khi đi qua PgBouncer transaction pooling. Giá trị là hằng code-owned, không
# bao giờ từ input ngoài (nối f-string vì SET không nhận bind parameter).
WORK_MEM_INCREMENTAL = "64MB"
WORK_MEM_REBUILD = "128MB"


def _conn():
    return connections[TARGET_ALIAS]


def _set_local_work_mem(cur, value: str) -> None:
    cur.execute(f"SET LOCAL work_mem = '{value}'")


def read_watermark(table_name: str):
    with _conn().cursor() as cur:
        cur.execute("SELECT moc FROM ai_meta.moc_dong_bo WHERE ten_bang = %s", [table_name])
        row = cur.fetchone()
    return row[0] if row else None


def write_watermark(table_name: str, moc) -> None:
    with _conn().cursor() as cur:
        cur.execute(
            "INSERT INTO ai_meta.moc_dong_bo (ten_bang, moc) VALUES (%s, %s)"
            " ON CONFLICT (ten_bang) DO UPDATE SET moc = EXCLUDED.moc",
            [table_name, moc],
        )


def read_watermark_id(table_name: str) -> int:
    with _conn().cursor() as cur:
        cur.execute("SELECT moc_id FROM ai_meta.moc_dong_bo WHERE ten_bang = %s", [table_name])
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else 0


def write_watermark_id(table_name: str, moc_id: int) -> None:
    with _conn().cursor() as cur:
        cur.execute(
            "INSERT INTO ai_meta.moc_dong_bo (ten_bang, moc_id) VALUES (%s, %s)"
            " ON CONFLICT (ten_bang) DO UPDATE SET moc_id = EXCLUDED.moc_id",
            [table_name, moc_id],
        )


def upsert_by_ids(mart: MartTable, ids: list[int]) -> int:
    """DELETE theo key rồi INSERT-select — một transaction.

    DELETE+INSERT thay vì ON CONFLICT: (1) không phải liệt kê 40 cột hai lần,
    (2) dòng RỜI KHỎI scope (xoá mềm, thành đơn mirror) được dọn đúng — ON
    CONFLICT không xoá được dòng đã mất khỏi SELECT.

    Note: f-string interpolation of mart.name, key_column, source_key is safe
    because they are code-owned constants from sql/*.py modules, never user input.
    """
    if not ids:
        return 0
    select = mart.select_sql(f"{mart.source_key} = ANY(%(ids)s)")
    with transaction.atomic(using=TARGET_ALIAS):
        with _conn().cursor() as cur:
            _set_local_work_mem(cur, WORK_MEM_INCREMENTAL)
            cur.execute(
                f"DELETE FROM {mart.name} WHERE {mart.key_column} = ANY(%(ids)s)",
                {"ids": ids},
            )
            cur.execute(f"INSERT INTO {mart.name} {select}", {"ids": ids})
            return cur.rowcount


def full_rebuild(mart: MartTable) -> int:
    select = mart.select_sql("TRUE")
    with transaction.atomic(using=TARGET_ALIAS):
        with _conn().cursor() as cur:
            _set_local_work_mem(cur, WORK_MEM_REBUILD)
            cur.execute(f"TRUNCATE {mart.name}")
            cur.execute(f"INSERT INTO {mart.name} {select}")
            return cur.rowcount


def candidate_ids(mart: MartTable, cutoff) -> list[int]:
    if mart.candidate_ids_sql is None:
        return []
    with _conn().cursor() as cur:
        cur.execute(mart.candidate_ids_sql, {"cutoff": cutoff})
        return [row[0] for row in cur.fetchall()]


def log_run(run_id, table_name, started_at, finished_at, rows, status, error="") -> None:
    try:
        with _conn().cursor() as cur:
            cur.execute(
                "INSERT INTO ai_meta.nhat_ky_chay"
                " (run_id, ten_bang, bat_dau, ket_thuc, so_dong, trang_thai, loi)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [run_id, table_name, started_at, finished_at, rows, status, error[:2000]],
            )
    except Exception:
        logger.warning("ghi ai_meta.nhat_ky_chay cho %s thất bại", table_name, exc_info=True)
