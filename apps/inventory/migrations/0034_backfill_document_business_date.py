import logging

from django.db import migrations

from apps.inventory.application.services.stock_document_date_backfill import (
    backfill_document_business_date,
)

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    # Log lại số dòng đã nắn: đây là lần ghi đè dữ liệu sổ kho lịch sử, mà chiều
    # ngược là noop nên không còn dấu vết nào khác về phạm vi tác động.
    stats = backfill_document_business_date(schema_editor.connection)
    logger.info("0034 backfill document business date: %s", stats)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("inventory", "0033_stockdocline_qty_positive")]

    operations = [
        # Không có chiều ngược: giá trị cũ (ngày DUYỆT) chính là thứ sai mà
        # migration này đang sửa.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
