"""Bỏ 5 index trùng tuyệt đối (audit 2026-09-23 T2/P2-3):
- facebook_ads.fb_ad_page_idx / fb_ad_campaign_idx ≡ FK auto (328kB/160kB),
- facebook_ad_campaigns.fb_camp_acc_idx / fb_camp_product_idx ≡ FK auto,
- facebook_apps.fb_app_app_id_idx ≡ unique index của app_id.

facebook_ads / facebook_ad_campaigns là bảng sync ghi liên tục (105k/57k
UPDATE mỗi 20h đo prod) → DROP INDEX CONCURRENTLY, atomic=False (khuôn
orders/0109). facebook_apps vài dòng → RemoveIndex thường.
"""

from django.db import migrations


def _drop_concurrently(model_name: str, table: str, name: str, column: str):
    return migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunSQL(
                sql=f"DROP INDEX CONCURRENTLY IF EXISTS {name}",
                reverse_sql=(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({column})"
                ),
            ),
        ],
        state_operations=[migrations.RemoveIndex(model_name=model_name, name=name)],
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("facebook_ads", "0010_campaign_product_source"),
    ]

    operations = [
        _drop_concurrently("facebookad", "facebook_ads", "fb_ad_page_idx", "page_id"),
        _drop_concurrently("facebookad", "facebook_ads", "fb_ad_campaign_idx", "campaign_id"),
        _drop_concurrently(
            "facebookadcampaign", "facebook_ad_campaigns", "fb_camp_acc_idx", "ad_account_id"
        ),
        _drop_concurrently(
            "facebookadcampaign", "facebook_ad_campaigns", "fb_camp_product_idx", "product_id"
        ),
        migrations.RemoveIndex(model_name="facebookapp", name="fb_app_app_id_idx"),
    ]
