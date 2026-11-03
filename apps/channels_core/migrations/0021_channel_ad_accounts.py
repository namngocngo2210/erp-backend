# Audit 2026-07 DB #6 — thay 2 ArrayField fb/google_ads_accounts bằng bảng nối
# channel_ad_accounts. Reversible: reverse đổ ngược mapping về ArrayField.
# Id mồ côi (ad account đã bị xoá) bị BỎ QUA khi copy + log số lượng — fix gốc
# bug "ads phồng".
import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models


def _clean_ids(raw_ids, valid_ids: set[int]) -> tuple[list[int], int]:
    """Chuẩn hoá 1 mảng id: ép int, bỏ trùng (giữ thứ tự), bỏ id mồ côi.

    Returns (kept_ids, orphan_count). Tách hàm thuần để unit-test được
    (test_channel_ad_accounts.py) mà không cần chạy lại migration.
    """
    seen: set[int] = set()
    kept: list[int] = []
    orphans = 0
    for raw in raw_ids or []:
        try:
            account_id = int(raw)
        except (TypeError, ValueError):
            continue
        if account_id in seen:
            continue
        seen.add(account_id)
        if account_id not in valid_ids:
            orphans += 1
            continue
        kept.append(account_id)
    return kept, orphans


def copy_arrays_to_mapping(apps, schema_editor):
    Channel = apps.get_model("channels_core", "Channel")
    ChannelAdAccount = apps.get_model("channels_core", "ChannelAdAccount")
    FacebookAdAccount = apps.get_model("facebook_ads", "FacebookAdAccount")
    GoogleAdsAccount = apps.get_model("google_ads", "GoogleAdsAccount")

    valid_fb = set(FacebookAdAccount.objects.values_list("id", flat=True))
    valid_google = set(GoogleAdsAccount.objects.values_list("id", flat=True))

    rows = []
    orphan_fb = 0
    orphan_google = 0
    for channel in Channel.objects.only("id", "fb_ads_accounts", "google_ads_accounts"):
        fb_ids, skipped = _clean_ids(channel.fb_ads_accounts, valid_fb)
        orphan_fb += skipped
        rows.extend(
            ChannelAdAccount(channel_id=channel.id, provider="fb", account_id=acc_id, position=pos)
            for pos, acc_id in enumerate(fb_ids)
        )
        google_ids, skipped = _clean_ids(channel.google_ads_accounts, valid_google)
        orphan_google += skipped
        rows.extend(
            ChannelAdAccount(channel_id=channel.id, provider="google", account_id=acc_id, position=pos)
            for pos, acc_id in enumerate(google_ids)
        )
    ChannelAdAccount.objects.bulk_create(rows)
    if orphan_fb or orphan_google:
        print(
            f"[channels_core.0021] bỏ qua id ad-account mồ côi: fb={orphan_fb}, google={orphan_google}"
        )


def copy_mapping_to_arrays(apps, schema_editor):
    Channel = apps.get_model("channels_core", "Channel")
    ChannelAdAccount = apps.get_model("channels_core", "ChannelAdAccount")

    fb_map: dict[int, list[int]] = {}
    google_map: dict[int, list[int]] = {}
    for link in ChannelAdAccount.objects.order_by("channel_id", "position", "id"):
        target = fb_map if link.provider == "fb" else google_map
        target.setdefault(link.channel_id, []).append(link.account_id)
    for channel in Channel.objects.only("id"):
        channel.fb_ads_accounts = fb_map.get(channel.id, [])
        channel.google_ads_accounts = google_map.get(channel.id, [])
        channel.save(update_fields=["fb_ads_accounts", "google_ads_accounts"])


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0020_channel_code_active_uniq"),
        # Cần model ad account để lọc id mồ côi trong data migration.
        ("facebook_ads", "0003_alter_facebookadaccount_table_and_more"),
        ("google_ads", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChannelAdAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "provider",
                    models.CharField(
                        choices=[("fb", "Facebook Ads"), ("google", "Google Ads")], max_length=10
                    ),
                ),
                (
                    "account_id",
                    models.BigIntegerField(
                        help_text="PK của FacebookAdAccount (provider=fb) hoặc GoogleAdsAccount (provider=google)."
                    ),
                ),
                (
                    "position",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Giữ thứ tự list id đúng như client gửi lên (contract ArrayField cũ).",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ad_account_links",
                        to="channels_core.channel",
                    ),
                ),
            ],
            options={
                "db_table": "channel_ad_accounts",
                "ordering": ["position", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="channeladaccount",
            index=models.Index(fields=["provider", "account_id"], name="chan_adacc_prov_acc_idx"),
        ),
        migrations.AddConstraint(
            model_name="channeladaccount",
            constraint=models.UniqueConstraint(
                fields=("channel", "provider", "account_id"), name="chan_adacc_uniq"
            ),
        ),
        migrations.RunPython(copy_arrays_to_mapping, copy_mapping_to_arrays),
        migrations.RemoveField(
            model_name="channel",
            name="fb_ads_accounts",
        ),
        migrations.RemoveField(
            model_name="channel",
            name="google_ads_accounts",
        ),
    ]
