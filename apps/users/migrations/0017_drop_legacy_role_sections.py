from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0016_backfill_user_departments"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="role",
        ),
        migrations.RemoveField(
            model_name="department",
            name="allowed_sections",
        ),
    ]
