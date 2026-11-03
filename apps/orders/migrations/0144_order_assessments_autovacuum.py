"""Hạ ngưỡng autovacuum/autoanalyze cho order_assessments xuống 5% (audit
2026-09-23 T2/P2-1 — mục treo từ audit 08-19).

Bảng 141MB (đo prod 23/09), được UPDATE liên tục theo từng lần sale đổi trạng
thái/nhu cầu; với scale_factor mặc định 0.2 visibility map mốc quá lâu. Cùng
khuôn 0107 (orders) và customers/0032. ALTER TABLE ... SET (storage parameter)
chỉ lấy SHARE UPDATE EXCLUSIVE — không chặn đọc/ghi.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0143_order_fb_campaign_id"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE order_assessments SET (
                autovacuum_vacuum_scale_factor = 0.05,
                autovacuum_analyze_scale_factor = 0.05
            );
            """,
            reverse_sql="""
            ALTER TABLE order_assessments RESET (
                autovacuum_vacuum_scale_factor,
                autovacuum_analyze_scale_factor
            );
            """,
        ),
    ]
