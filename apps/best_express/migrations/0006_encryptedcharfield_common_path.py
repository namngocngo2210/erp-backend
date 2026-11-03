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
        ('best_express', '0005_shipment_created_by'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bestexpressaccount',
            name='password',
            field=apps.common.fields.EncryptedCharField(max_length=512),
        ),
        migrations.AlterField(
            model_name='bestexpressaccount',
            name='token',
            field=apps.common.fields.EncryptedCharField(blank=True, default='', max_length=4096),
        ),
        migrations.AlterField(
            model_name='bestexpressaccount',
            name='ems_key',
            field=apps.common.fields.EncryptedCharField(blank=True, default='', max_length=512),
        ),
        migrations.AlterField(
            model_name='bestexpressaccount',
            name='webhook_token',
            field=apps.common.fields.EncryptedCharField(blank=True, default='', help_text='Bearer token tĩnh mình sinh, gửi sale Best cấu hình webhook', max_length=512),
        ),
    ]
