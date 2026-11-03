import django.contrib.postgres.fields
from django.db import migrations, models

# Postgres cấm subquery trực tiếp trong transform expression của
# ALTER COLUMN ... USING ("cannot use subquery in transform expression"),
# nên phần split/trim/drop-empty/truncate (match _parse_slip_codes semantics)
# phải nằm trong 1 SQL function riêng, gọi function đó từ USING, rồi drop
# function ngay sau khi dùng xong (không để lại rác trong schema).
FORWARD_SQL = """
-- LOCAL: chỉ trong transaction của migration này. SET thường sẽ dính lại trên
-- connection sau COMMIT và làm các migration sau đó abort sớm khi chờ khoá.
SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION __split_return_slip_code_0101(input_value varchar)
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

ALTER TABLE order_recalls
    ALTER COLUMN return_slip_code DROP DEFAULT,
    ALTER COLUMN return_slip_code TYPE varchar(64)[]
        USING __split_return_slip_code_0101(return_slip_code);
ALTER TABLE order_recalls ALTER COLUMN return_slip_code SET DEFAULT '{}';
ALTER TABLE order_recalls ALTER COLUMN return_slip_code SET NOT NULL;

DROP FUNCTION __split_return_slip_code_0101(varchar);
"""

REVERSE_SQL = """
-- Cùng lý do với FORWARD_SQL: LOCAL để không dính lại trên connection sau COMMIT.
SET LOCAL lock_timeout = '5s';

ALTER TABLE order_recalls
    ALTER COLUMN return_slip_code DROP DEFAULT,
    ALTER COLUMN return_slip_code TYPE varchar(64)
        USING left(COALESCE(array_to_string(return_slip_code, ', '), ''), 64);
ALTER TABLE order_recalls ALTER COLUMN return_slip_code SET DEFAULT '';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0100_alter_order_sku"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="orderrecall",
                    name="return_slip_code",
                    field=django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=64),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
            ],
        ),
    ]
