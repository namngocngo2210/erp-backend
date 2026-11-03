import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kiotviet", "0014_kiotvietrevenuelarkrecord_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="kiotvietchannel",
            name="branch_names",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.TextField(),
                blank=True,
                default=list,
                help_text="Tên chi nhánh, khớp thứ tự với branch_ids (branch_names[i] là tên của branch_ids[i])",
                size=None,
                verbose_name="Branch Names",
            ),
        ),
    ]
