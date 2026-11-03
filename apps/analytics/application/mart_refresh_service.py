"""Refresh các materialized view trong schema `dwh`.

Hai điều dễ sai và đều gây chết ngay lần chạy đầu:

1. Matview tạo `WITH NO DATA` CHƯA được nạp lần nào thì
   `REFRESH ... CONCURRENTLY` báo lỗi. Phải đọc `pg_class.relispopulated`
   để chọn nhánh.
2. `customer_360` đọc hai matview kia — Postgres KHÔNG tự suy ra phụ thuộc,
   thứ tự trong `MART_ORDER` là bắt buộc.
"""

import logging
import time
import uuid

from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.common.db_guards import statement_timeout

logger = logging.getLogger(__name__)

MART_ORDER: tuple[str, ...] = (
    "dim_customer_profile",
    "fct_order_enriched",
    "customer_360",
)

# Phụ thuộc ngược dòng: mart ở khoá ĐỌC dữ liệu từ các mart liệt kê trong
# value. Khi một phụ thuộc hỏng (lỗi hoặc bị skip), mart này phải SKIP thay vì
# refresh trên snapshot CŨ của phụ thuộc đó — nếu không customer_360 vẫn
# "refresh thành công" nhưng mang chân dung của hôm qua, /api/analytics/
# coverage trả 200 với số sai, và dwh_reconcile không phát hiện được (3 phép
# kiểm của nó không đụng tới cột chân dung). dim_customer_profile và
# fct_order_enriched độc lập với nhau — mart nào hỏng, mart kia vẫn refresh.
MART_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "dim_customer_profile": (),
    "fct_order_enriched": (),
    "customer_360": ("dim_customer_profile", "fct_order_enriched"),
}


class MartRefreshFailed(RuntimeError):
    """Có ít nhất một matview refresh hỏng, hoặc bị skip do phụ thuộc hỏng."""


def is_populated(name: str) -> bool:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relispopulated FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'dwh' AND c.relname = %s",
            [name],
        )
        row = cur.fetchone()
    if row is None:
        raise MartRefreshFailed(f"matview dwh.{name} không tồn tại")
    return bool(row[0])


def _record_refresh(
    name: str, run_id: uuid.UUID, started_at, finished_at, elapsed: float, status: str
) -> None:
    """Ghi một dòng vào dwh.mart_refresh_log. KHÔNG được làm hỏng refresh: dù
    mart vừa refresh xong (status='ok') hay vừa lỗi (status='failed'), mất một
    dòng log không đáng để đánh hỏng cả lượt chạy."""
    try:
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO dwh.mart_refresh_log"
                " (mart_name, run_id, started_at, finished_at, duration_seconds, status)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [name, run_id, started_at, finished_at, elapsed, status],
            )
    except Exception:
        logger.warning("ghi dwh.mart_refresh_log cho %s thất bại", name, exc_info=True)


def refresh_mart(name: str, run_id: uuid.UUID | None = None) -> float:
    """Refresh một matview, trả về số giây đã chạy.

    `run_id`: đóng dấu lần chạy CỐ KẾT mà lần refresh này thuộc về (xem
    `refresh_all_marts` và docstring migration 0007). Mặc định sinh `run_id`
    mới nếu gọi độc lập (vd. test gọi trực tiếp `refresh_mart` ngoài
    `refresh_all_marts`) — một mart refresh đơn lẻ tự nó đã là một "lần chạy"
    của riêng nó.
    """
    if run_id is None:
        run_id = uuid.uuid4()
    concurrently = "CONCURRENTLY " if is_populated(name) else ""
    started_at = timezone.now()
    started = time.monotonic()
    timeout = getattr(settings, "DWH_REFRESH_TIMEOUT_SECONDS", 900)
    try:
        with statement_timeout(timeout), connection.cursor() as cur:
            cur.execute(f"REFRESH MATERIALIZED VIEW {concurrently}dwh.{name}")
    except Exception:
        finished_at = timezone.now()
        elapsed = time.monotonic() - started
        _record_refresh(name, run_id, started_at, finished_at, elapsed, status="failed")
        raise
    elapsed = time.monotonic() - started
    finished_at = timezone.now()
    logger.info(
        "refreshed dwh.%s in %.1fs (concurrently=%s)",
        name, elapsed, bool(concurrently),
    )
    _record_refresh(name, run_id, started_at, finished_at, elapsed, status="ok")
    return elapsed


def refresh_all_marts() -> dict[str, float]:
    """Refresh đúng thứ tự. Chạy hết những mart CÓ THỂ chạy rồi mới raise, để
    một matview hỏng không che mất kết quả của các matview độc lập với nó.
    Mart phụ thuộc vào một mart hỏng/skip thì bị SKIP — không refresh trên
    snapshot cũ của phụ thuộc đó (xem MART_DEPENDENCIES).

    Một `run_id` DUY NHẤT được sinh cho cả lượt gọi này và đóng dấu lên mọi
    dòng log mà nó ghi (kể cả dòng 'failed') — đây là thứ `last_refresh_boundary`
    dùng để nhận diện "một lượt chạy cố kết" thay vì trộn dòng log mới nhất
    của từng mart từ NHIỀU lần chạy khác nhau (xem migration 0007).
    """
    run_id = uuid.uuid4()
    timings: dict[str, float] = {}
    failed: list[str] = []
    skipped: list[str] = []
    for name in MART_ORDER:
        blocking = [dep for dep in MART_DEPENDENCIES[name] if dep in failed or dep in skipped]
        if blocking:
            logger.error(
                "skip dwh.%s: phụ thuộc hỏng/bị skip (%s)", name, ", ".join(blocking)
            )
            skipped.append(name)
            continue
        try:
            timings[name] = refresh_mart(name, run_id=run_id)
        except Exception:
            logger.exception("refresh dwh.%s failed", name)
            failed.append(name)
    if failed or skipped:
        parts = []
        if failed:
            parts.append(f"hỏng: {', '.join(failed)}")
        if skipped:
            parts.append(f"skip do phụ thuộc hỏng: {', '.join(skipped)}")
        raise MartRefreshFailed("; ".join(parts))
    return timings


def last_refresh_boundary():
    """Mốc as-of cho việc đối chiếu: thời điểm SỚM NHẤT trong lượt refresh CỐ KẾT
    gần nhất.

    Dùng `started_at` chứ không phải `finished_at` — xem docstring migration 0005.

    Trả None trừ khi TẤT CẢ đều đúng:
      1. Có ít nhất một dòng log `status = 'ok'`.
      2. `run_id` của dòng 'ok' MỚI NHẤT có đủ một dòng 'ok' cho MỌI mart trong
         MART_ORDER — không được trộn dòng của các `run_id` khác nhau (lỗ hổng
         gốc: 5 đêm job chết liên tiếp không sinh dòng log mới, nhưng MỖI mart
         vẫn có "dòng mới nhất của riêng nó" từ 5 đêm trước — nếu chỉ xét theo
         mart mà không xét run_id, MIN trên ba dòng lẻ đó vẫn ra một mốc "đủ
         coverage" giả, và dwh_reconcile báo khớp trên kho dữ liệu cũ 5 ngày).
         Một mart có dòng 'failed' trong run_id đó KHÔNG được tính — refresh
         thất bại không phải là coverage.
      3. Mọi mart trong MART_ORDER đang `relispopulated` (qua `is_populated`).
         Che hố migration 0006: matview `customer_360` được DROP + CREATE
         `WITH NO DATA`, còn `mart_refresh_log` là bảng thật, giữ nguyên các
         dòng log từ TRƯỚC migration — nếu chỉ xét run_id/status, boundary sẽ
         "khoẻ mạnh" trong khi customer_360 thực ra đang rỗng, và câu SELECT
         tiếp theo của dwh_reconcile ném OperationalError thô thay vì thông
         báo rõ ràng.

    Việc lượt chạy cố kết cuối cùng có thể vẫn CŨ (job chết nhiều đêm, chưa có
    lượt chạy mới nào để so sánh) được chặn riêng ở `dwh_reconcile`
    (DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS) — cố ý tách lớp: hàm này chỉ trả lời
    "có một lượt chạy cố kết, đủ mọi mart, đã nạp dữ liệu hay không", còn
    "lượt đó có quá cũ hay không" là quyết định của caller.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT run_id FROM dwh.mart_refresh_log"
            " WHERE status = 'ok' ORDER BY started_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return None
    latest_run_id = row[0]

    with connection.cursor() as cur:
        cur.execute(
            "SELECT mart_name, started_at FROM dwh.mart_refresh_log"
            " WHERE run_id = %s AND status = 'ok'",
            [latest_run_id],
        )
        rows_in_run = dict(cur.fetchall())
    if not set(MART_ORDER).issubset(rows_in_run):
        return None

    if not all(is_populated(name) for name in MART_ORDER):
        return None

    return min(rows_in_run[name] for name in MART_ORDER)
