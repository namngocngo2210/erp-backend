from django.db import migrations, models


class Migration(migrations.Migration):
    """Thêm cột phân loại đơn KOC.

    Tách riêng khỏi AddIndexConcurrently (xem 0126_order_koc_type_index) — mỗi câu
    DDL của AddIndexConcurrently tự commit ngoài transaction (atomic = False), nếu
    gộp chung file mà index chết giữa chừng thì cột ĐÃ tồn tại nhưng migration CHƯA
    được ghi vào django_migrations, chạy lại sẽ chết ngay ở AddField với lỗi
    "column ... already exists". AddField default hằng là thao tác metadata-only
    trên PG 11+ nên chạy atomic bình thường là đủ, không cần CONCURRENTLY.
    """

    dependencies = [
        ("orders", "0124_orderrecall_recall_code_array"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="koc_order_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("sample_gift", "Tặng mẫu"),
                    ("renewal", "Gia hạn"),
                    ("recall", "Thu hồi"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
