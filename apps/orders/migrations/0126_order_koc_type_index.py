from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """Index cho bộ lọc koc_order_type trên trang KOC.

    CONCURRENTLY vì bảng orders lớn: CREATE INDEX thường khoá ghi suốt thời gian quét
    bảng. Đổi lại migration này KHÔNG chạy trong transaction (atomic = False) — hỏng
    giữa chừng sẽ để lại index INVALID, phải DROP rồi chạy lại.

    Partial: gần như toàn bộ orders có koc_order_type rỗng (chỉ đơn KOC được gắn
    nhãn), nên loại các dòng rỗng ra khỏi index cho nhỏ gọn.
    """

    atomic = False

    dependencies = [
        ("orders", "0125_order_koc_order_type"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["koc_order_type", "-created_at"],
                condition=~Q(koc_order_type=""),
                name="order_koc_type_created_idx",
            ),
        ),
    ]
