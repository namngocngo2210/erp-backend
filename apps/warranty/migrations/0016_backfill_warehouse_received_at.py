"""CHỈ BÁO CÁO — migration này KHÔNG sửa dữ liệu.

Trước đây nó tự nắn `warehouse_received_at`. Đã bỏ: trường này NGƯỜI DÙNG SỬA
ĐƯỢC và không có tín hiệu nào trong DB phân biệt được "cron ghi sai ngày" với
"nhân viên gõ tay trùng đúng ngày cron khớp" (xem docstring
`apps.warranty.received_at_backfill`). Deploy không được phép âm thầm đè dữ liệu
người dùng dựa trên một suy đoán không chắc chắn.

Nên: migration liệt kê ứng viên vào log ở mức WARNING, còn việc nắn do người vận
hành quyết định và tự chạy `manage.py backfill_warranty_received_at --apply`.
"""
import logging

from django.db import migrations

from apps.warranty.received_at_backfill import backfill_warehouse_received_at

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    # apply=False ⇒ chỉ SELECT, KHÔNG ghi. Log ở mức WARNING (không phải INFO)
    # để dòng này không chìm giữa log deploy: nó là việc CẦN NGƯỜI làm tiếp.
    rows = backfill_warehouse_received_at(schema_editor.connection, apply=False)
    logger.warning(
        "0016 (CHỈ BÁO CÁO — KHÔNG có dữ liệu nào bị thay đổi): %s hành trình là "
        "ỨNG VIÊN nắn warehouse_received_at (journey_id, ngày_hiện_tại, "
        "ngày_đề_xuất)=%s",
        len(rows),
        rows,
    )
    logger.warning(
        "0016 VIỆC CẦN LÀM: duyệt từng dòng ở trên (giá trị hiện tại có thể do "
        "NHÂN VIÊN gõ tay, không phải cron ghi). Rà lại bằng "
        "`python manage.py backfill_warranty_received_at`, và CHỈ khi đã xác nhận "
        "mới chạy `python manage.py backfill_warranty_received_at --apply`."
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("warranty", "0015_journey_best_tracking_status"),
        # SQL đọc thẳng bảng `pending_shipment_scans` của app inventory. Không
        # khai báo thì `migrate warranty` trên DB trắng đổ vì bảng chưa tồn tại
        # — hiện chỉ chạy được nhờ may mắn thứ tự leaf node. Không tạo vòng:
        # không migration nào của inventory phụ thuộc warranty.
        ("inventory", "0017_pendingshipmentscan"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
