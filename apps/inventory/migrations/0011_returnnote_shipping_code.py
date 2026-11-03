from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0010_merge_20260703_1340"),
    ]

    operations = [
        migrations.AddField(
            model_name="returnnote",
            name="shipping_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="returnnote",
            index=models.Index(
                fields=["shipping_code"], name="returnnote_shipcode_idx"
            ),
        ),
    ]
