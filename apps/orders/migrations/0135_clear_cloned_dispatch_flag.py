import logging

from django.db import migrations

from apps.orders.application.services.cloned_dispatch_flag_backfill import (
    clear_cloned_dispatch_flags,
)

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    # Log danh sách order_id: chiều ngược là noop nên đây là dấu vết duy nhất về
    # phạm vi tác động. Đo trên prod 2026-08-23: 7 đơn (3 đơn KShip 21–22/08 là ca
    # kho báo "quét mã báo đã xuất kho dù chưa xuất", 1 đơn 03/08, 3 đơn huỷ 29/07).
    order_ids = clear_cloned_dispatch_flags(schema_editor.connection)
    logger.info(
        "0135 xoá cờ 'đã xuất kho' copy nhầm từ nút Nhân bản: %s đơn %s",
        len(order_ids),
        order_ids,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0134_move_product_catalog_to_catalog_app"),
        # Điều kiện lọc đọc `stock_movements` + `pending_shipment_scans`; phải chắc
        # hai bảng đã tồn tại ở thời điểm chạy.
        ("inventory", "0041_stock_flow_note"),
    ]

    operations = [
        # Không đảo được: cờ cũ là dữ liệu SAI (đơn chưa hề trừ tồn), khôi phục lại
        # đúng bằng việc dựng lại lỗi. Rollback giữ nguyên trạng thái đã dọn.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
