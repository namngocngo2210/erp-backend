"""Hạ ngưỡng autovacuum/autoanalyze cho bảng customers xuống 5%.

Cùng lý do với orders.0107: đo trên prod 2026-07-22, customers có 136,566 dead tuple
(16.4%) và visibility map cũ tới mức Index Only Scan trên khoá chính phải xuống heap
705,528/750,729 dòng. Với 831k dòng, scale_factor mặc định 0.2 nghĩa là phải tích đủ
~166k dead tuple mới được autovacuum chạm tới — quá thưa cho một bảng mà các query
báo cáo dựa vào index-only scan.

Xem docs/report-precompute-timeout-2026-07-22.md.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0031_contactactivity_kind_operation"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE customers SET (
                autovacuum_vacuum_scale_factor = 0.05,
                autovacuum_analyze_scale_factor = 0.05
            );
            """,
            reverse_sql="""
            ALTER TABLE customers RESET (
                autovacuum_vacuum_scale_factor,
                autovacuum_analyze_scale_factor
            );
            """,
        ),
    ]
