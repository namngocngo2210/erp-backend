"""Bảng ai.dong_don_hang — mart nóng 1 dòng/sản phẩm trong đơn (spec §3.2).

DDL + COMMENT tiếng Việt cho toàn bộ cột — test_contract.py xác nhận không
thiếu cột nào. Transform sinh dữ liệu nằm ở infrastructure/sql/dong_don_hang.py,
migration này chỉ tạo khung bảng rỗng; nạp dữ liệu là việc của loaders.

key_column="don_id" (không phải PK dong_id): upsert xoá theo don_id để nạp lại
toàn bộ dòng của đơn — cách này xử lý được item bị xoá khỏi đơn.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.dong_don_hang (
    dong_id bigint PRIMARY KEY,
    don_id bigint,
    ma_don text,
    ngay_tao_ngay date,
    sku text,
    ten_sp text,
    nhom_sp_text text,
    so_luong int,
    don_gia numeric,
    thanh_tien numeric
);
CREATE INDEX IF NOT EXISTS dong_don_hang_don_id_idx ON ai.dong_don_hang (don_id);
CREATE INDEX IF NOT EXISTS dong_don_hang_sku_idx ON ai.dong_don_hang (sku);
CREATE INDEX IF NOT EXISTS dong_don_hang_ngay_tao_ngay_idx ON ai.dong_don_hang (ngay_tao_ngay);

COMMENT ON TABLE ai.dong_don_hang IS
 'Mỗi dòng = 1 sản phẩm trong 1 đơn. JOIN ai.don_hang qua don_id để lọc theo trạng thái/GTC hoặc nhóm theo sản phẩm. Đơn bị xoá SẠCH toàn bộ dòng (hard-delete hết order_items) có thể còn giữ dòng cũ ở đây cho tới lần rebuild đêm kế tiếp, vì đồng bộ tăng dần chỉ nạp lại don_id còn dòng, không phát hiện được đơn đã rỗng dòng.';
COMMENT ON COLUMN ai.dong_don_hang.dong_id IS 'Id dòng (order_item) trong hệ vận hành — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.dong_don_hang.don_id IS 'Id đơn trong hệ vận hành — dùng JOIN ngược lại ai.don_hang';
COMMENT ON COLUMN ai.dong_don_hang.ma_don IS 'Mã đơn hiển thị trên CRM, vd DL12345';
COMMENT ON COLUMN ai.dong_don_hang.ngay_tao_ngay IS 'Ngày tạo đơn quy đổi theo múi giờ Việt Nam (UTC+7) — mốc chuẩn để nhóm báo cáo theo ngày';
COMMENT ON COLUMN ai.dong_don_hang.sku IS 'Mã SKU/VCN của sản phẩm trên dòng — NULL nếu sku rỗng';
COMMENT ON COLUMN ai.dong_don_hang.ten_sp IS 'Tên sản phẩm theo ghi nhận trên dòng đơn — có thể lệch với tên sản phẩm hiện tại nếu được sửa sau';
COMMENT ON COLUMN ai.dong_don_hang.nhom_sp_text IS 'Nhóm sản phẩm (danh mục) theo phân loại nội bộ — NULL nếu sản phẩm chưa được phân nhóm';
COMMENT ON COLUMN ai.dong_don_hang.so_luong IS 'Số lượng sản phẩm trên dòng';
COMMENT ON COLUMN ai.dong_don_hang.don_gia IS 'Đơn giá (unit_price) trên dòng';
COMMENT ON COLUMN ai.dong_don_hang.thanh_tien IS 'Thành tiền (line_total) = số lượng × đơn giá trước chiết khấu dòng';
"""

DROP = """
DROP TABLE IF EXISTS ai.dong_don_hang;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0005_don_hang")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
