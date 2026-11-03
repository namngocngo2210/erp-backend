"""Bảng ai.san_pham — mart đêm 1 dòng/SP (Sprint 2, Task 12).
DDL + COMMENT tiếng Việt cho toàn bộ cột — test_contract.py xác nhận không
thiếu cột nào. Transform sinh dữ liệu nằm ở infrastructure/sql/san_pham.py,
migration này chỉ tạo khung bảng rỗng; nạp dữ liệu là việc của loaders.

10 cột, đúng thứ tự SELECT (INSERT theo vị trí, không liệt kê tên cột).
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.san_pham (
    san_pham_id bigint PRIMARY KEY,
    sku text,
    ten_sp text,
    ten_hien_thi text,
    nhom_sp_text text,
    gia_ban numeric,
    dang_kinh_doanh boolean,
    la_combo boolean,
    thanh_phan_combo text[],
    dong_bo_luc timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS san_pham_sku_idx ON ai.san_pham (sku);

COMMENT ON TABLE ai.san_pham IS
 'Mỗi dòng = 1 sản phẩm — danh mục SP cơ bản với giá bán, trạng thái kinh doanh, và thông tin combo (nếu có)';
COMMENT ON COLUMN ai.san_pham.san_pham_id IS 'Id sản phẩm (catalog_products.id) — khóa chính';
COMMENT ON COLUMN ai.san_pham.sku IS 'Mã SKU sản phẩm — duy nhất không rỗng';
COMMENT ON COLUMN ai.san_pham.ten_sp IS 'Tên sản phẩm từ catalog — tên chính thức từ KiotViet';
COMMENT ON COLUMN ai.san_pham.ten_hien_thi IS 'Tên hiển thị trên trang public (product_profiles.display_name) — NULL nếu rỗng/không được cấu hình (thay vào đó dùng ten_sp)';
COMMENT ON COLUMN ai.san_pham.nhom_sp_text IS 'Tên nhóm sản phẩm — NULL nếu không được gán nhóm';
COMMENT ON COLUMN ai.san_pham.gia_ban IS 'Giá bán sản phẩm';
COMMENT ON COLUMN ai.san_pham.dang_kinh_doanh IS 'Trạng thái kinh doanh: true = đang bán, false = ngừng bán';
COMMENT ON COLUMN ai.san_pham.la_combo IS 'Cờ combo — true nếu sản phẩm là combo (có ≥1 thành phần trong product_components), false nếu là sản phẩm lẻ. Catalog CỐ Ý không có is_combo flag, mart vật chất hoá luật đó từ product_components.';
COMMENT ON COLUMN ai.san_pham.thanh_phan_combo IS 'Mảng SKU các sản phẩm thành phần (nếu la_combo=true) — mảng rỗng nếu là sản phẩm lẻ';
COMMENT ON COLUMN ai.san_pham.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè vào ai.san_pham — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.san_pham;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0008_cuoc_goi")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
