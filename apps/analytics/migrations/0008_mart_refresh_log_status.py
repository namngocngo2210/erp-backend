"""Thêm `status` vào `dwh.mart_refresh_log` — ghi lại cả lượt refresh THẤT BẠI.

Trước migration này chỉ có refresh THÀNH CÔNG mới sinh dòng log (xem
`_record_refresh` trong `mart_refresh_service.py`, gọi SAU khi `REFRESH
MATERIALIZED VIEW` chạy xong không lỗi). Một mart refresh hỏng thì biến mất
khỏi lịch sử hoàn toàn — không có dấu vết nào để chẩn đoán sau này ngoài log
ứng dụng (thường đã xoay vòng mất). `status` ('ok' / 'failed') để lại bằng
chứng ngay trong DB.

`last_refresh_boundary()` (xem `mart_refresh_service.py`) chỉ đếm dòng
`status = 'ok'` khi xét "mọi mart có mặt trong lần chạy mới nhất" — một dòng
`'failed'` chứng minh mart đó KHÔNG có dữ liệu mới ở lần chạy này, không được
tính là coverage.

Mặc định `'ok'`: mọi dòng đã tồn tại trước migration này đều là refresh thành
công thật (đường ghi log cũ chỉ chạy sau REFRESH thành công), gán `'ok'` cho
chúng là đúng sự thật, không phải giá trị lấp chỗ trống.
"""

from django.db import migrations

FORWARD_SQL = """
ALTER TABLE dwh.mart_refresh_log
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'ok';

ALTER TABLE dwh.mart_refresh_log
    ADD CONSTRAINT mart_refresh_log_status_check CHECK (status IN ('ok', 'failed'));
"""

REVERSE_SQL = """
ALTER TABLE dwh.mart_refresh_log DROP CONSTRAINT IF EXISTS mart_refresh_log_status_check;
ALTER TABLE dwh.mart_refresh_log DROP COLUMN IF EXISTS status;
"""


class Migration(migrations.Migration):
    dependencies = [("analytics", "0007_mart_refresh_log_run_id")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
