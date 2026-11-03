from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("foxia", "0005_foxialeadlink"),
    ]

    operations = [
        migrations.AddField(
            model_name="foxialinkchannel",
            name="default_source_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
