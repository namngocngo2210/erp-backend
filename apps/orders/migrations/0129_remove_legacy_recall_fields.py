from django.db import migrations


class Migration(migrations.Migration):
    """Xoá 3 cột thu hồi khỏi order_recalls sau khi 0128 đã chuyển sang bảng mới.

    Reverse dựng lại nguyên trạng cột + index GIN (rỗng) để 0128 có chỗ ghi ngược.
    """

    dependencies = [
        ("orders", "0128_copy_recall_entries"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="orderrecall", name="order_recall_code_gin"),
        migrations.RemoveField(model_name="orderrecall", name="recall_code"),
        migrations.RemoveField(model_name="orderrecall", name="recall_created_date"),
        migrations.RemoveField(model_name="orderrecall", name="recall_received_date"),
    ]
