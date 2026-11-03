"""Tạo 3 schema của AI mart. Tách riêng để CREATE SCHEMA chạy đúng một lần,
các migration bảng sau chỉ phụ thuộc vào nó (tiền lệ: analytics/0001_dwh_schema).

`ai` — bảng contract Hermes đọc. `ai_stg` — staging. `ai_meta` — watermark + nhật ký.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE SCHEMA IF NOT EXISTS ai;"
                        " CREATE SCHEMA IF NOT EXISTS ai_stg;"
                        " CREATE SCHEMA IF NOT EXISTS ai_meta;"
                    ),
                    reverse_sql=(
                        "DROP SCHEMA IF EXISTS ai CASCADE;"
                        " DROP SCHEMA IF EXISTS ai_stg CASCADE;"
                        " DROP SCHEMA IF EXISTS ai_meta CASCADE;"
                    ),
                )
            ]
        )
    ]
