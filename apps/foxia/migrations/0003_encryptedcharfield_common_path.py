"""Chốt lại deconstruct path của EncryptedCharField sang apps.common.fields.

Field chuyển từ apps/kiotviet/models.py về apps/common/fields.py (audit
2026-08-19 mục 1.6). AlterField này NO-OP trên DB: cùng class, cùng db_type
varchar — chỉ ghi path mới vào migration state. Xác nhận bằng sqlmigrate:
không có ALTER TABLE.
"""

import apps.common.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('foxia', '0002_foxiaorderlink_foxiaorderline_foxiashipment_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='foxiaconnection',
            name='api_key',
            field=apps.common.fields.EncryptedCharField(max_length=1024),
        ),
    ]
