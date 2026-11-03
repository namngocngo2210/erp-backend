"""Hạ ngưỡng autovacuum/autoanalyze cho bảng orders xuống 5%.

Phát hiện khi điều tra sự cố statement timeout 2026-07-22: bảng orders (~540k dòng,
714MB heap, >25 index) CHƯA TỪNG được autovacuum hay autoanalyze lần nào, đang mang
18k dead tuple. Với scale_factor mặc định 0.2, bảng phải tích đủ ~108k dead tuple mới
được chạm tới.

Hệ quả đo được: visibility map cũ khiến "Index Only Scan" trên customers phải xuống
heap 705,528/750,729 dòng — mất sạch ý nghĩa của index-only. Ở 5% ngưỡng còn ~27k, đủ
để VM luôn tươi mà không làm autovacuum chạy quá dày trên một bảng ghi nhiều.

ALTER TABLE ... SET (storage parameter) chỉ lấy SHARE UPDATE EXCLUSIVE lock — không
chặn đọc/ghi, an toàn chạy trên prod.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0106_floating_pool"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE orders SET (
                autovacuum_vacuum_scale_factor = 0.05,
                autovacuum_analyze_scale_factor = 0.05
            );
            """,
            reverse_sql="""
            ALTER TABLE orders RESET (
                autovacuum_vacuum_scale_factor,
                autovacuum_analyze_scale_factor
            );
            """,
        ),
    ]
