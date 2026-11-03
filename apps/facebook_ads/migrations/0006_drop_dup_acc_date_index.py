"""Drop fb_dailyspend_acc_date_idx: trùng tuyệt đối với index của fb_dailyspend_acc_date_uniq.

Audit DB 2026-08-19 mục 3d: index trùng lặp tuyệt đối với unique constraint cùng cột — mỗi UPDATE
phải maintain gấp đôi trên bảng ghi nóng (sync spend hằng ngày). Đã đối chiếu: planner dùng bản còn lại.
CONCURRENTLY -> atomic=False; fail giữa chừng để lại index INVALID — dọn theo
docs/runbooks/2026-07-26-drop-dead-indexes.md.
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("facebook_ads", "0005_page_grain"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(model_name="facebookadaccountdailyspend", name="fb_dailyspend_acc_date_idx"),
        ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS fb_dailyspend_acc_date_idx;",
                    reverse_sql="""CREATE INDEX CONCURRENTLY IF NOT EXISTS fb_dailyspend_acc_date_idx ON facebook_ad_account_daily_spends (ad_account_id, date);""",
                ),
        ],
        ),
    ]
