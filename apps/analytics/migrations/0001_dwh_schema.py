"""Tạo schema `dwh` — nơi chứa toàn bộ materialized view phân tích.

Tách riêng khỏi migration matview để `CREATE SCHEMA` chạy đúng một lần và các
migration matview sau chỉ phụ thuộc vào nó.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="CREATE SCHEMA IF NOT EXISTS dwh;",
                    reverse_sql="DROP SCHEMA IF EXISTS dwh CASCADE;",
                )
            ]
        )
    ]
