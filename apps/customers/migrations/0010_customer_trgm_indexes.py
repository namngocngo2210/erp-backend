from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0009_customer_deleted_at"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="customer",
            index=GinIndex(fields=["phone_raw"], name="cust_phone_raw_trgm_idx", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=GinIndex(fields=["phone_normalized"], name="cust_phone_norm_trgm_idx", opclasses=["gin_trgm_ops"]),
        ),
    ]
