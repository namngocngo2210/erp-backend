"""Index cho đường rebuild snapshot phân khúc TĂNG DẦN.

`SegmentSnapshotService.affected_customer_ids` quét ba nguồn theo `updated_at`:
``orders``, ``order_assessments``, ``customers``. Nguồn thứ hai đã có
``ord_assessment_updated_idx``; hai nguồn còn lại chưa có index nào trên
``updated_at`` (``orders`` chỉ có partial index ``ord_resale_delivered_upd_idx``
giới hạn ở ``status=3 AND deleted_at IS NULL``, không phục vụ được câu quét này).

Thiếu hai index này thì việc tăng dần trở nên VÔ NGHĨA: mỗi chu kỳ đổi một lần
seq scan `orders` + `customers` lấy một lần rebuild toàn bộ — không cải thiện gì.

``CREATE INDEX CONCURRENTLY`` (và do đó ``atomic = False``): ``orders`` là bảng
nóng nhất hệ thống, ``CREATE INDEX`` thường giữ khoá ghi suốt thời gian dựng.
Đặt cả hai lệnh trong MỘT migration của app ``customers`` — theo đúng tiền lệ
``0016_loyalcustomernote_resale_status_and_more`` — thay vì tách một migration
riêng bên ``orders``, để không tạo thêm một nhánh lá thứ hai trong cây migration
của app đó.
"""

from django.db import migrations


class Migration(migrations.Migration):

    atomic = False  # cần cho CREATE INDEX CONCURRENTLY

    dependencies = [
        ("customers", "0032_customers_autovacuum_thresholds"),
        # RunSQL chạm bảng "orders" → orders phải migrate xong trước, nếu không
        # test DB có thể dựng migration này khi bảng chưa tồn tại.
        ("orders", "0118_productprofile_productprofileimage_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_updated_at_idx "
                "ON orders (updated_at);"
            ),
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_updated_at_idx;",
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS customer_updated_at_idx "
                "ON customers (updated_at);"
            ),
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS customer_updated_at_idx;",
        ),
    ]
