"""Bảng nguồn chat trong ai_stg — dạng LOCAL (test/dev).

Trên prod, `manage.py ai_mart_chat_fdw` DROP các bảng này rồi tạo lại thành
FOREIGN TABLE trỏ sang DB crawler. Migration dùng IF NOT EXISTS nên lần migrate
sau thấy foreign table cùng tên là bỏ qua — không giẫm lên cầu FDW đang chạy.
"""

from django.db import migrations

from apps.ai_mart.infrastructure.chat_source import create_local_tables_sql, drop_tables_sql


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0014_chat_tu_dien")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=create_local_tables_sql(), reverse_sql=drop_tables_sql())
            ]
        )
    ]
