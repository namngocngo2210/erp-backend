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
        ('kiotviet', '0016_kiotvietchannel_old_sync_branch_map'),
    ]

    operations = [
        migrations.AlterField(
            model_name='kiotvietchannel',
            name='client_secret',
            field=apps.common.fields.EncryptedCharField(max_length=255, null=True, verbose_name='Client Secret'),
        ),
        migrations.AlterField(
            model_name='kiotvietchannel',
            name='password',
            field=apps.common.fields.EncryptedCharField(blank=True, max_length=255, null=True, verbose_name='Password'),
        ),
    ]
