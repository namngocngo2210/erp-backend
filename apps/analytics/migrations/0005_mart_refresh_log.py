"""Bảng ghi mốc refresh của từng mart.

Là BẢNG THẬT, không phải matview: nó lưu sự kiện chứ không phải dẫn xuất, và phải
ghi được ngay trong lúc matview đang refresh.

`started_at` là thứ `dwh_reconcile` dùng làm mốc as-of, KHÔNG phải `finished_at`:
matview chụp ảnh dữ liệu tại thời điểm BẮT ĐẦU, nên bản ghi sinh ra giữa lúc refresh
chạy sẽ không lọt vào mart.
"""

from django.db import migrations

FORWARD_SQL = """
CREATE TABLE IF NOT EXISTS dwh.mart_refresh_log (
    id               bigserial PRIMARY KEY,
    mart_name        text        NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz NOT NULL,
    duration_seconds numeric(10, 3) NOT NULL
);
CREATE INDEX IF NOT EXISTS mart_refresh_log_name_started_idx
    ON dwh.mart_refresh_log (mart_name, started_at DESC);
"""

REVERSE_SQL = "DROP TABLE IF EXISTS dwh.mart_refresh_log;"


class Migration(migrations.Migration):
    dependencies = [("analytics", "0004_customer_360")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
