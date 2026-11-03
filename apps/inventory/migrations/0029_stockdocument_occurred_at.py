from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0028_stockmove_occurred_index")]

    operations = [
        migrations.AddField(
            model_name="stockdocument",
            name="occurred_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
