"""Báo cáo mức độ SỬ DỤNG THẬT của từng index trên một bảng, để quyết định drop.

Bối cảnh: `orders` là bảng ghi nóng nhất hệ thống và đang gánh hàng chục index.
Mỗi UPDATE phải maintain TẤT CẢ index của bảng, nên index không ai đọc là chi phí
ghi thuần tuý. Nhưng "không ai đọc" phải chứng minh bằng SỐ, không phải bằng cảm
giác — command này lấy số đó từ `pg_stat_user_indexes`.

    python manage.py report_index_usage
    python manage.py report_index_usage --table customers --min-scans 100

Command đặt trong `apps.orders` (chứ không phải `apps.common`) vì Django CHỈ nạp
management command từ app có trong `INSTALLED_APPS`, mà `apps.common` cố ý là package
tiện ích thuần (không models, không AppConfig) nên không được cài. `apps.orders` là
app thật, đã chứa sẵn các command dữ liệu khác, và cũng chính là bảng mặc định ở đây.

⚠️ ĐỌC KÈM UPTIME: `idx_scan` là bộ đếm TÍCH LUỸ, bị reset khi Postgres restart
hoặc khi ai đó gọi `pg_stat_reset()`. `idx_scan = 0` trên một instance vừa khởi
động lại 10 phút trước KHÔNG chứng minh được gì cả. Vì vậy command luôn in
`pg_postmaster_start_time()` và số ngày tích luỹ lên đầu output — ngưỡng kết luận
là ≥ 7 ngày uptime (xem docs/runbooks/2026-07-26-drop-dead-indexes.md).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

# Ngưỡng uptime tối thiểu để `idx_scan = 0` có ý nghĩa thống kê. Dưới mức này,
# bộ đếm chưa kịp chứng kiến đủ chu kỳ nghiệp vụ (báo cáo tuần, cron cuối tuần...).
_MIN_UPTIME_DAYS = 7

_QUERY = """
    SELECT
        s.indexrelname                                  AS index_name,
        s.idx_scan                                      AS idx_scan,
        pg_relation_size(s.indexrelid)                  AS size_bytes,
        pg_size_pretty(pg_relation_size(s.indexrelid))  AS size_pretty,
        i.indisvalid                                    AS is_valid,
        i.indisunique                                   AS is_unique
    FROM pg_stat_user_indexes s
    JOIN pg_index i ON i.indexrelid = s.indexrelid
    WHERE s.relname = %s
      AND s.idx_scan >= %s
    ORDER BY s.idx_scan ASC, pg_relation_size(s.indexrelid) DESC
"""


class Command(BaseCommand):
    help = "Liệt kê index của một bảng kèm idx_scan + size, đọc cùng uptime Postgres."

    def add_arguments(self, parser):
        parser.add_argument(
            "--table",
            default="orders",
            help="Tên bảng cần soi (mặc định: orders — bảng ghi nóng nhất).",
        )
        parser.add_argument(
            "--min-scans",
            type=int,
            default=0,
            help="Chỉ hiện index có idx_scan >= giá trị này (mặc định 0 = hiện tất cả).",
        )

    def handle(self, *args, **options):
        table = options["table"]
        min_scans = options["min_scans"]

        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_postmaster_start_time()")
            started_at = cursor.fetchone()[0]
            cursor.execute(_QUERY, [table, min_scans])
            rows = cursor.fetchall()

        uptime_days = (timezone.now() - started_at).total_seconds() / 86400

        self.stdout.write(f"Table: {table}")
        self.stdout.write(f"Postgres started at: {started_at:%Y-%m-%d %H:%M:%S %z}")
        self.stdout.write(f"Stats accumulated over: {uptime_days:.1f} days")
        if uptime_days < _MIN_UPTIME_DAYS:
            # Cảnh báo chứ không chặn: người vận hành vẫn có thể muốn xem size.
            self.stdout.write(
                self.style.WARNING(
                    f"CAUTION: uptime < {_MIN_UPTIME_DAYS} days — idx_scan=0 does NOT prove an index is unused."
                )
            )
        self.stdout.write("")

        header = f"{'INDEX':<40} {'IDX_SCAN':>12} {'SIZE':>10}  FLAGS"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        if not rows:
            self.stdout.write(f"(no indexes matching min-scans={min_scans})")
            return

        total_bytes = 0
        for name, idx_scan, size_bytes, size_pretty, is_valid, is_unique in rows:
            total_bytes += size_bytes
            flags = []
            if is_unique:
                # Index unique KHÔNG được drop dù idx_scan=0: nó đang thực thi ràng
                # buộc toàn vẹn dữ liệu, không phải để tăng tốc đọc.
                flags.append("UNIQUE/constraint - do NOT drop")
            if not is_valid:
                # Dấu vết của một CREATE/DROP INDEX CONCURRENTLY thất bại giữa chừng.
                flags.append("INVALID - leftover from failed CONCURRENTLY, drop it")
            self.stdout.write(
                f"{name:<40} {idx_scan:>12} {size_pretty:>10}  {', '.join(flags)}"
            )

        self.stdout.write("")
        self.stdout.write(f"{len(rows)} indexes, total {total_bytes / 1024 / 1024:.1f} MB")
