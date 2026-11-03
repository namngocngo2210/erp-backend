"""Watermark + nhật ký chạy của ETL. Nằm ở ai_meta — hermes_ro KHÔNG thấy."""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai_meta.moc_dong_bo (
    ten_bang text PRIMARY KEY,
    moc      timestamptz,
    moc_id   bigint
);
COMMENT ON TABLE ai_meta.moc_dong_bo IS
    'Watermark ETL: moc cho bảng theo dw_updated_at, moc_id cho bảng append-only (stock_movements)';

CREATE TABLE IF NOT EXISTS ai_meta.nhat_ky_chay (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL,
    ten_bang text NOT NULL,
    bat_dau timestamptz NOT NULL,
    ket_thuc timestamptz,
    so_dong int,
    trang_thai text NOT NULL,
    loi text NOT NULL DEFAULT ''
);
COMMENT ON TABLE ai_meta.nhat_ky_chay IS 'Nhật ký từng lượt nạp một bảng mart. trang_thai: ok | failed';
CREATE INDEX IF NOT EXISTS nhat_ky_chay_bang_thoi_gian_idx
    ON ai_meta.nhat_ky_chay (ten_bang, bat_dau DESC);
"""

DROP = """
DROP TABLE IF EXISTS ai_meta.nhat_ky_chay;
DROP TABLE IF EXISTS ai_meta.moc_dong_bo;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0003_tu_dien_ma")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
