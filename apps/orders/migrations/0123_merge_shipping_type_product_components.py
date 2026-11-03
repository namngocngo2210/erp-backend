from django.db import migrations


class Migration(migrations.Migration):
    """Gộp hai nhánh migration cùng tách ra từ 0120.

    `0121_product_components` (MR combo sản phẩm) và `0121_order_shipping_type` +
    `0122_order_shipping_type_index` (MR đơn bán trực tiếp) được tạo song song trên
    hai nhánh, merge vào master gần nhau nên đồ thị có HAI leaf → mọi lệnh
    `migrate`/`makemigrations` chết với "Conflicting migrations detected".

    Dùng migration gộp rỗng thay vì sửa `dependencies` của một trong hai nhánh: môi
    trường nào đã chạy một nhánh mà chưa chạy nhánh kia sẽ dính
    `InconsistentMigrationHistory` (Django coi là "migration đã apply trước
    dependency của nó"). Migration rỗng thì thứ tự apply thực tế nào cũng hợp lệ.
    """

    dependencies = [
        ("orders", "0121_product_components"),
        ("orders", "0122_order_shipping_type_index"),
    ]

    operations = []
