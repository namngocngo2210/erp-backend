import logging

from django.db import migrations

from apps.inventory.application.services.occurred_at_backfill import (
    backfill_from_pending_scans,
)

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    # Log lại số dòng đã nắn: đây là lần ghi đè dữ liệu tài chính lịch sử, mà
    # chiều ngược là noop nên không còn dấu vết nào khác về phạm vi tác động.
    stats = backfill_from_pending_scans(schema_editor.connection)
    logger.info("0027 backfill occurred_at: %s", stats)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("inventory", "0026_occurred_at_not_null")]

    operations = [
        # Không có chiều ngược: giá trị cũ (= created_at) là thứ SAI mà migration
        # này đang sửa, khôi phục lại không có ý nghĩa nghiệp vụ nào.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
