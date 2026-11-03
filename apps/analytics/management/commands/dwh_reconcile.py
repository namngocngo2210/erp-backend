"""Đối chiếu số của mart `dwh` với nguồn OLTP.

Tiêu chí hoàn thành Sprint 1. Chạy sau mỗi lần refresh trên prod.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.analytics.application.mart_refresh_service import last_refresh_boundary
from apps.analytics.sql.gtc import gtc_where
from apps.common.order_scope import sql_exclude_mirror_orders

_CHECKS = {
    "so_don_gtc": (
        f"SELECT count(*) FROM orders o"
        f" WHERE {gtc_where('o')} AND o.created_at < %(boundary)s",
        "SELECT count(*) FROM dwh.fct_order_enriched WHERE is_gtc",
    ),
    "doanh_thu_gtc": (
        f"SELECT coalesce(sum(o.total_after_discount), 0) FROM orders o"
        f" WHERE {gtc_where('o')} AND o.created_at < %(boundary)s",
        "SELECT coalesce(sum(total_after_discount), 0)"
        " FROM dwh.fct_order_enriched WHERE is_gtc",
    ),
    "so_khach_co_don": (
        f"SELECT count(DISTINCT o.customer_id) FROM orders o"
        f" WHERE o.deleted_at IS NULL AND o.customer_id IS NOT NULL"
        f" AND {sql_exclude_mirror_orders('o')} AND o.created_at < %(boundary)s",
        "SELECT count(*) FROM dwh.customer_360",
    ),
}


class Command(BaseCommand):
    help = "Đối chiếu số của mart dwh với nguồn OLTP, tính đến mốc refresh gần nhất"

    def handle(self, *args, **options):
        boundary = last_refresh_boundary()
        if boundary is None:
            raise CommandError(
                "Không xác định được mốc đối chiếu vì mart chưa refresh đủ —"
                " hãy chạy `refresh_dwh_marts` trước khi chạy dwh_reconcile."
            )

        # Chặn "kho chết vẫn qua cổng": last_refresh_boundary() chỉ đảm bảo lượt
        # refresh cố kết gần nhất có đủ mọi mart, KHÔNG đảm bảo lượt đó gần đây.
        # Nếu job 04:25 chết nhiều đêm liên tiếp, lượt cố kết cuối cùng vẫn "đủ
        # mart" nhưng ngày càng cũ — reconcile sẽ so mart cũ với nguồn-tính-đến-
        # mốc-cũ, khớp tuyệt đối, và báo "OK" trên một kho dữ liệu đã chết. 36h vì
        # job chạy MỖI ĐÊM một lần: bỏ lỡ một đêm (tới ~24h) vẫn chấp nhận được,
        # bỏ lỡ hai đêm liên tiếp mới là vấn đề thật cần người can thiệp.
        max_age_hours = getattr(settings, "DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS", 36)
        age = timezone.now() - boundary
        max_age = timezone.timedelta(hours=max_age_hours)
        if age > max_age:
            raise CommandError(
                f"Mốc refresh gần nhất ({boundary}) đã cũ {age} —"
                f" vượt trần {max_age_hours}h (DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS)."
                " Kho dữ liệu có thể đã chết nhiều đêm liên tiếp — kiểm tra job"
                " refresh_dwh_marts trước khi tin kết quả đối chiếu."
            )

        self.stdout.write(f"Đối chiếu tính đến: {boundary}")

        mismatches = []
        with connection.cursor() as cur:
            for label, (src_sql, mart_sql) in _CHECKS.items():
                cur.execute(src_sql, {"boundary": boundary})
                src = cur.fetchone()[0]
                cur.execute(mart_sql)
                mart = cur.fetchone()[0]
                ok = src == mart
                self.stdout.write(
                    f"{label:20} nguồn={src:>15}  mart={mart:>15}  "
                    f"{'OK' if ok else 'LỆCH'}"
                )
                if not ok:
                    mismatches.append(f"{label}: nguồn={src} mart={mart}")
        if mismatches:
            raise CommandError("Mart lệch nguồn — " + "; ".join(mismatches))
        self.stdout.write(self.style.SUCCESS("Mart khớp nguồn."))
