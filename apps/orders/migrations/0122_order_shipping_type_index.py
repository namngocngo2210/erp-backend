from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    """Index cho bộ lọc shipping_type trên trang Đơn hàng.

    CONCURRENTLY vì bảng orders lớn: CREATE INDEX thường khoá ghi suốt thời gian quét
    bảng. Đổi lại migration này KHÔNG chạy trong transaction (atomic = False) — hỏng
    giữa chừng sẽ để lại index INVALID, phải DROP rồi chạy lại.

    Cột ghép với created_at DESC vì bộ lọc luôn đi kèm khoảng thời gian và danh sách
    đơn sắp xếp theo created_at giảm dần.
    """

    atomic = False

    dependencies = [
        ("orders", "0121_order_shipping_type"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["shipping_type", "-created_at"], name="order_shiptype_created_idx"
            ),
        ),
    ]
