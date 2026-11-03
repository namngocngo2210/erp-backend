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
        ('google_ads', '0003_drop_dup_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='googleadsconfig',
            name='developer_token',
            field=apps.common.fields.EncryptedCharField(max_length=512, verbose_name='Developer Token'),
        ),
        migrations.AlterField(
            model_name='googleadsconfig',
            name='client_secret',
            field=apps.common.fields.EncryptedCharField(max_length=512, verbose_name='OAuth Client Secret'),
        ),
        migrations.AlterField(
            model_name='googleadsconfig',
            name='refresh_token',
            field=apps.common.fields.EncryptedCharField(max_length=1024, verbose_name='Refresh Token'),
        ),
    ]
