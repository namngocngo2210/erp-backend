"""Chuẩn hoá chiến dịch Facebook thành bảng riêng.

Trước: ``campaign_id``/``campaign_name`` là hai cột chuỗi lặp trên TỪNG dòng
``facebook_ads``. Sau: bảng ``facebook_ad_campaigns`` + FK.

THỨ TỰ TRONG FILE NÀY LÀ CÓ CHỦ ĐÍCH và khác thứ tự makemigrations sinh ra: phải
tạo bảng + FK, CHẠY XONG data migration, rồi mới xoá hai cột cũ. makemigrations xếp
RemoveField lên đầu — chạy vậy là mất sạch dữ liệu chiến dịch trước khi kịp chép.

Đây là migration BREAKING: sau khi chạy, code cũ đọc ``ad.campaign_id`` (chuỗi) sẽ
lỗi. Phải migrate + roll code CÙNG NHỊP, không migrate trước rồi thong thả roll.
"""

import django.db.models.deletion
from django.db import migrations, models


def build_campaigns(apps, schema_editor):
    """Dựng bảng chiến dịch từ dữ liệu đang nằm rải trên facebook_ads.

    Tên chiến dịch lấy từ ad có ``last_seen_at`` MỚI NHẤT, không phải MAX(name):
    khảo sát prod 2026-08-24 có 9 campaign_id mang nhiều tên khác nhau (tên đã đổi
    bên Facebook, ad cũ giữ tên cũ). MAX(name) trả tên theo thứ tự alphabet — vô
    nghĩa, và sẽ ghim tên cũ lên báo cáo.
    """
    FacebookAd = apps.get_model("facebook_ads", "FacebookAd")
    FacebookAdCampaign = apps.get_model("facebook_ads", "FacebookAdCampaign")

    # None sắp sau cùng khi sort giảm dần theo last_seen_at → ad chưa từng thấy lại
    # không cướp mất tên của ad còn sống.
    latest: dict[str, dict] = {}
    rows = FacebookAd.objects.exclude(campaign_id="").values(
        "campaign_id", "campaign_name", "ad_account_id", "last_seen_at",
    )
    for row in rows.iterator(chunk_size=2000):
        key = row["campaign_id"]
        current = latest.get(key)
        if current is None:
            latest[key] = row
            continue
        seen, current_seen = row["last_seen_at"], current["last_seen_at"]
        if current_seen is None or (seen is not None and seen > current_seen):
            latest[key] = row

    FacebookAdCampaign.objects.bulk_create(
        [
            FacebookAdCampaign(
                fb_campaign_id=key,
                name=row["campaign_name"] or "",
                # Bản sao TKQC: mọi bản sao quy về một dòng chiến dịch, giữ TKQC của
                # ad mới nhất. Không mất số vì chi tiêu bám theo ad, không bám TKQC.
                ad_account_id=row["ad_account_id"],
                last_seen_at=row["last_seen_at"],
                first_seen_at=row["last_seen_at"],
            )
            for key, row in latest.items()
        ],
        batch_size=1000,
    )

    # Ở BƯỚC NÀY field FK còn mang tên tạm `campaign_fk_tmp` (xem chú thích ở
    # operations). Gán nhầm sang `campaign_id` là ghi đè cột chuỗi cũ, không phải FK.
    by_key = dict(FacebookAdCampaign.objects.values_list("fb_campaign_id", "id"))
    ads = list(FacebookAd.objects.exclude(campaign_id="").values("id", "campaign_id"))
    for start in range(0, len(ads), 1000):
        batch = ads[start : start + 1000]
        FacebookAd.objects.bulk_update(
            [
                FacebookAd(id=ad["id"], campaign_fk_tmp_id=by_key[ad["campaign_id"]])
                for ad in batch
                if ad["campaign_id"] in by_key
            ],
            ["campaign_fk_tmp"],
        )


def restore_campaign_columns(apps, schema_editor):
    """Chép ngược FK về hai cột chuỗi để rollback không mất dữ liệu."""
    FacebookAd = apps.get_model("facebook_ads", "FacebookAd")
    # Chiều ngược đã hoàn tác RenameField nên field lại là `campaign_fk_tmp`.
    rows = list(
        FacebookAd.objects.exclude(campaign_fk_tmp__isnull=True).values(
            "id", "campaign_fk_tmp__fb_campaign_id", "campaign_fk_tmp__name",
        )
    )
    for start in range(0, len(rows), 1000):
        batch = rows[start : start + 1000]
        FacebookAd.objects.bulk_update(
            [
                FacebookAd(
                    id=row["id"],
                    campaign_id=row["campaign_fk_tmp__fb_campaign_id"],
                    campaign_name=row["campaign_fk_tmp__name"] or "",
                )
                for row in batch
            ],
            ["campaign_id", "campaign_name"],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("facebook_ads", "0007_encryptedcharfield_common_path"),
    ]

    operations = [
        migrations.CreateModel(
            name="FacebookAdCampaign",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "fb_campaign_id",
                    models.CharField(max_length=64, unique=True, verbose_name="Campaign ID"),
                ),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                ("first_seen_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                (
                    "ad_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="campaigns",
                        to="facebook_ads.facebookadaccount",
                    ),
                ),
            ],
            options={
                "verbose_name": "Facebook Ad Campaign",
                "verbose_name_plural": "Facebook Ad Campaigns",
                "db_table": "facebook_ad_campaigns",
            },
        ),
        # Tên tạm: cột cũ `campaign_id` (varchar) còn đang chiếm chỗ, nên FK phải mang
        # tên khác rồi mới đổi lại sau khi xoá cột cũ. Không có bước này thì Django
        # dựng bảng state với hai field trùng attname.
        migrations.AddField(
            model_name="facebookad",
            name="campaign_fk_tmp",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ads_tmp",
                to="facebook_ads.facebookadcampaign",
            ),
        ),
        migrations.RunPython(build_campaigns, restore_campaign_columns),
        migrations.RemoveField(model_name="facebookad", name="campaign_id"),
        migrations.RemoveField(model_name="facebookad", name="campaign_name"),
        migrations.RenameField(
            model_name="facebookad", old_name="campaign_fk_tmp", new_name="campaign",
        ),
        migrations.AlterField(
            model_name="facebookad",
            name="campaign",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ads",
                to="facebook_ads.facebookadcampaign",
            ),
        ),
        migrations.AddIndex(
            model_name="facebookad",
            index=models.Index(fields=["campaign"], name="fb_ad_campaign_idx"),
        ),
        migrations.AddIndex(
            model_name="facebookadcampaign",
            index=models.Index(fields=["ad_account"], name="fb_camp_acc_idx"),
        ),
    ]
