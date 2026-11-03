from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0009_backfill_user_kiotviet_mappings"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="lark_open_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="user",
            name="last_no_answer_reminder_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
