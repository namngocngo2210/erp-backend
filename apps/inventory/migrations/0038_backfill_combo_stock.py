import logging

from django.db import migrations

from apps.inventory.application.services.combo_stock_backfill import (
    backfill_combo_stock,
    combo_outstanding_report,
)

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    # Ảnh chụp TRƯỚC/SAU: đây là lần chuyển sổ tồn kho, chiều ngược là noop nên
    # log này là dấu vết kiểm toán duy nhất về phạm vi tác động.
    before = combo_outstanding_report()
    logger.info("0038 combo backfill — outstanding trên SKU combo TRƯỚC: %s", before)
    stats = backfill_combo_stock()
    logger.info("0038 combo backfill — kết quả: %s", stats)
    after = combo_outstanding_report()
    logger.info("0038 combo backfill — outstanding trên SKU combo SAU: %s", after)
    if after:
        # Không raise: tồn kho đã chuyển xong, chặn migration ở đây chỉ làm kẹt
        # deploy. Nhưng phải kêu to — còn sót nghĩa là có combo được khai thêm
        # giữa chừng hoặc có bút toán mới chen vào.
        logger.warning("0038 combo backfill — CÒN SÓT outstanding trên SKU combo: %s", after)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("inventory", "0037_drop_dup_stockitem_indexes"),
        # Backfill đọc `product_components`; bảng do app orders tạo ở 0121.
        ("orders", "0121_product_components"),
    ]

    operations = [
        # Không có chiều ngược: bút toán chuyển sổ là dữ liệu sổ kho append-only,
        # đảo lại phải bằng một bút toán mới chứ không phải xoá dấu vết.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
