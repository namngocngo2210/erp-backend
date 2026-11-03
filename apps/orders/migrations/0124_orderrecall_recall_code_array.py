import django.contrib.postgres.fields
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models

# Cùng lý do với 0101 (return_slip_code): Postgres cấm subquery trong transform
# expression của ALTER COLUMN ... USING, nên phần split/trim/drop-empty/truncate
# (khớp semantics _parse_slip_codes) phải nằm trong 1 SQL function riêng, gọi từ
# USING, rồi drop ngay sau khi dùng — không để lại rác trong schema.
FORWARD_SQL = """
-- LOCAL: chỉ trong transaction của migration này. SET thường sẽ dính lại trên
-- connection sau COMMIT và làm các migration sau đó abort sớm khi chờ khoá.
SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION __split_recall_code_0124(input_value varchar)
    RETURNS varchar(64)[] AS $$
        SELECT CASE
            WHEN input_value IS NULL OR btrim(input_value) = '' THEN ARRAY[]::varchar(64)[]
            ELSE COALESCE(
                (
                    SELECT array_agg(left(btrim(part), 64))
                    FROM unnest(string_to_array(input_value, ',')) AS part
                    WHERE btrim(part) <> ''
                ),
                ARRAY[]::varchar(64)[]
            )
        END;
    $$ LANGUAGE sql IMMUTABLE;

DROP INDEX IF EXISTS order_recall_code_idx;

ALTER TABLE order_recalls
    ALTER COLUMN recall_code DROP DEFAULT,
    ALTER COLUMN recall_code TYPE varchar(64)[]
        USING __split_recall_code_0124(recall_code);
ALTER TABLE order_recalls ALTER COLUMN recall_code SET DEFAULT '{}';
ALTER TABLE order_recalls ALTER COLUMN recall_code SET NOT NULL;

CREATE INDEX order_recall_code_gin ON order_recalls USING GIN (recall_code);

DROP FUNCTION __split_recall_code_0124(varchar);
"""

REVERSE_SQL = """
SET LOCAL lock_timeout = '5s';

DROP INDEX IF EXISTS order_recall_code_gin;

ALTER TABLE order_recalls
    ALTER COLUMN recall_code DROP DEFAULT,
    ALTER COLUMN recall_code TYPE varchar(64)
        USING left(COALESCE(array_to_string(recall_code, ', '), ''), 64);
ALTER TABLE order_recalls ALTER COLUMN recall_code SET DEFAULT '';

CREATE INDEX order_recall_code_idx ON order_recalls (recall_code);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0123_merge_shipping_type_product_components"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="orderrecall",
                    name="order_recall_code_idx",
                ),
                migrations.AlterField(
                    model_name="orderrecall",
                    name="recall_code",
                    field=django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=64),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                migrations.AddIndex(
                    model_name="orderrecall",
                    index=GinIndex(fields=["recall_code"], name="order_recall_code_gin"),
                ),
            ],
        ),
    ]
