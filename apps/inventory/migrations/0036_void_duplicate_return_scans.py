import logging

from django.db import migrations

from apps.inventory.application.services.return_scan_void_backfill import (
    void_dead_return_scans,
)

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    # Log số dòng đã thu hồi: chiều ngược là noop nên đây là dấu vết duy nhất về
    # phạm vi tác động. Đo trên prod 2026-08-17: 11 dòng / 9 mã.
    voided = void_dead_return_scans(schema_editor.connection)
    logger.info("0036 thu hồi %s dòng quét nhập hoàn đã chết", voided)


class Migration(migrations.Migration):

    dependencies = [("inventory", "0035_pendingscan_voided")]

    operations = [
        # Không đảo được: sau khi chạy, `voided_at` do migration này đóng dấu lẫn
        # với dòng do người dùng huỷ sau đó — xoá sạch là làm hiện lại cả những
        # dòng vừa được huỷ hợp lệ. Rollback giữ nguyên (không mất dữ liệu, chỉ là
        # màn hình vẫn sạch).
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
