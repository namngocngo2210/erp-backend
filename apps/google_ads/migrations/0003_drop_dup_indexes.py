"""Drop 2 index trùng tuyệt đối với unique constraint cùng cột (gads daily spend + campaign spend).

Audit DB 2026-08-19 mục 3d: index trùng lặp tuyệt đối với unique constraint cùng cột — mỗi UPDATE
phải maintain gấp đôi trên bảng ghi. Đã đối chiếu: planner dùng bản còn lại.
CONCURRENTLY -> atomic=False; fail giữa chừng để lại index INVALID — dọn theo
docs/runbooks/2026-07-26-drop-dead-indexes.md.
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("google_ads", "0002_campaign_grain"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(model_name="googleadsaccountdailyspend", name="gads_dailyspend_acc_date_idx"),
                migrations.RemoveIndex(model_name="googleadscampaigndailyspend", name="gads_camp_spend_cd_idx"),
        ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS gads_dailyspend_acc_date_idx;",
                    reverse_sql="""CREATE INDEX CONCURRENTLY IF NOT EXISTS gads_dailyspend_acc_date_idx ON google_ads_account_daily_spends (ad_account_id, date);""",
                ),
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS gads_camp_spend_cd_idx;",
                    reverse_sql="""CREATE INDEX CONCURRENTLY IF NOT EXISTS gads_camp_spend_cd_idx ON google_ads_campaign_daily_spends (campaign_id, date);""",
                ),
        ],
        ),
    ]
