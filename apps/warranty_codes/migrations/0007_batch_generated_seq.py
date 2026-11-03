from django.db import migrations, models
from django.db.models import F


def backfill_generated_seq(apps, schema_editor):
    # Mọi lô trước migration này đều sinh mã đồng bộ trong request → đã đủ mã.
    WarrantyCodeBatch = apps.get_model("warranty_codes", "WarrantyCodeBatch")
    WarrantyCodeBatch.objects.update(generated_seq=F("end_seq"))


class Migration(migrations.Migration):

    dependencies = [
        ("warranty_codes", "0006_product_fks_to_catalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="warrantycodebatch",
            name="generated_seq",
            field=models.BigIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_generated_seq, migrations.RunPython.noop),
    ]
