from django.db import migrations, models


def backfill_vat_tax_id(apps, schema_editor):
    # Giữ nguyên hành vi cũ cho kênh đang chạy: old=False → TaxId 3 (VAT 8%), old=True → không thuế.
    # Kênh HKD (không kê khai VAT) phải được đặt lại trống trong UI sau khi migrate.
    KiotVietChannel = apps.get_model("kiotviet", "KiotVietChannel")
    KiotVietChannel.objects.filter(old=False).update(vat_tax_id=3)


class Migration(migrations.Migration):
    dependencies = [
        ("kiotviet", "0017_encryptedcharfield_common_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="kiotvietchannel",
            name="vat_tax_id",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Mã thuế KiotViet gắn cho dòng hàng khi đẩy đơn (vd 3 = VAT 8%). Để trống = kênh không áp VAT.",
                null=True,
                verbose_name="VAT Tax ID",
            ),
        ),
        migrations.RunPython(backfill_vat_tax_id, migrations.RunPython.noop),
    ]
