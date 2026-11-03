from django.core.serializers.json import DjangoJSONEncoder
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add DjangoJSONEncoder to Job.payload + Job.result.

    Default JSONField encoder (json.JSONEncoder) cannot serialize datetime,
    Decimal, UUID — causing 503 on landing ingest when DRF validated_data
    contains those types. SQL no-op (encoder is Python-side only).
    """

    dependencies = [
        ("dispatch", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="job",
            name="payload",
            field=models.JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder),
        ),
        migrations.AlterField(
            model_name="job",
            name="result",
            field=models.JSONField(blank=True, encoder=DjangoJSONEncoder, null=True),
        ),
    ]
