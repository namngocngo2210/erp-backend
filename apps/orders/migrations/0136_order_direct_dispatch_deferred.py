from django.db import migrations, models


class Migration(migrations.Migration):
    """Thêm cột cờ "xuất sau" cho đơn bán trực tiếp.

    Tách riêng khỏi AddIndexConcurrently (xem 0137_order_direct_deferred_index) — mỗi câu
    DDL của AddIndexConcurrently tự commit ngoài transaction (atomic = False), nếu
    gộp chung file mà index chết giữa chừng thì cột ĐÃ tồn tại nhưng migration CHƯA
    được ghi vào django_migrations, chạy lại sẽ chết ngay ở AddField với lỗi
    "column ... already exists". AddField default hằng là thao tác metadata-only
    trên PG 11+ nên chạy atomic bình thường là đủ, không cần CONCURRENTLY.
    """

    dependencies = [
        ("orders", "0135_clear_cloned_dispatch_flag"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="direct_dispatch_deferred",
            field=models.BooleanField(default=False),
        ),
    ]
