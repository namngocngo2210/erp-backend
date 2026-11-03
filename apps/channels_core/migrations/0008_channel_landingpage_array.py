from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("channels_core", "0007_channel_deleted_at"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    ALTER TABLE channels_core_channel
                    ALTER COLUMN landingpage_source TYPE text[]
                    USING CASE
                      WHEN landingpage_source IS NULL OR btrim(landingpage_source) = '' THEN ARRAY[]::text[]
                      ELSE ARRAY[landingpage_source]
                    END;
                    """,
                    reverse_sql="""
                    ALTER TABLE channels_core_channel
                    ALTER COLUMN landingpage_source TYPE varchar(255)
                    USING CASE
                      WHEN landingpage_source IS NULL OR array_length(landingpage_source, 1) IS NULL THEN ''
                      ELSE landingpage_source[1]
                    END;
                    """,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="channel",
                    name="landingpage_source",
                    field=ArrayField(base_field=models.TextField(), blank=True, default=list),
                ),
            ],
        ),
    ]

