from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0044_seed_call_quality_export_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="zenify_email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
    ]
