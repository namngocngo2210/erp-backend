"""Bảng ai.ton_kho + ai.lich_su_kho — mart đêm tồn kho (Sprint 3, Task 15).
DDL + COMMENT tiếng Việt cho toàn bộ cột — test_contract.py xác nhận không
thiếu cột nào. Transform sinh dữ liệu nằm ở
infrastructure/sql/{ton_kho,lich_su_kho}.py, migration này chỉ tạo khung bảng
rỗng; nạp dữ liệu là việc của loaders.

ton_kho: 8 cột, đúng thứ tự SELECT (INSERT theo vị trí, không liệt kê tên cột).
lich_su_kho: 14 cột, đúng thứ tự SELECT — bảng append-only, xem
infrastructure/sql/lich_su_kho.py::sync_incremental về nhịp nạp tăng dần.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.ton_kho (
    ton_kho_id bigint PRIMARY KEY,
    kho_ten text,
    sku text,
    ten_sp text,
    trang_thai_ton_text text,
    so_luong integer,
    cap_nhat_luc timestamptz,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS ton_kho_sku_idx ON ai.ton_kho (sku);

COMMENT ON TABLE ai.ton_kho IS
 'Mỗi dòng = tồn hiện tại của 1 (kho, sản phẩm, trạng thái tồn) — chiếu (projection) của stock_items. Số liệu lịch sử/luỹ kế đọc ai.lich_su_kho, bảng này chỉ có số dư hiện tại';
COMMENT ON COLUMN ai.ton_kho.ton_kho_id IS 'Id dòng tồn (stock_items.id) — khóa chính';
COMMENT ON COLUMN ai.ton_kho.kho_ten IS 'Tên kho (warehouses.name)';
COMMENT ON COLUMN ai.ton_kho.sku IS 'Mã SKU sản phẩm';
COMMENT ON COLUMN ai.ton_kho.ten_sp IS 'Tên sản phẩm';
COMMENT ON COLUMN ai.ton_kho.trang_thai_ton_text IS 'Trạng thái tồn (nhãn tiếng Việt) — dịch từ stock_items.state qua ai.nhan(''trang_thai_ton'', ...): Bán được / Bảo hành / Lỗi';
COMMENT ON COLUMN ai.ton_kho.so_luong IS 'Số lượng tồn hiện tại — có thể âm (cho phép bán vượt tồn có cảnh báo, spec §4.2)';
COMMENT ON COLUMN ai.ton_kho.cap_nhat_luc IS 'Thời điểm dòng tồn này được cập nhật lần cuối (stock_items.updated_at) — mốc nghiệp vụ';
COMMENT ON COLUMN ai.ton_kho.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè vào ai.ton_kho — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';

CREATE TABLE IF NOT EXISTS ai.lich_su_kho (
    but_toan_id bigint PRIMARY KEY,
    ngay date,
    kho_ten text,
    sku text,
    ten_sp text,
    loai_bien_dong_text text,
    trang_thai_ton_text text,
    so_luong_thay_doi integer,
    ton_sau integer,
    loai_chung_tu_text text,
    chung_tu_id bigint,
    ma_don text,
    ghi_chu text,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS lich_su_kho_ngay_idx ON ai.lich_su_kho (ngay);
CREATE INDEX IF NOT EXISTS lich_su_kho_sku_idx ON ai.lich_su_kho (sku);
CREATE INDEX IF NOT EXISTS lich_su_kho_ma_don_idx ON ai.lich_su_kho (ma_don);

COMMENT ON TABLE ai.lich_su_kho IS
 'Sổ cái kho — BẤT BIẾN (append-only, 1 dòng = 1 bút toán stock_movements, không bao giờ UPDATE/DELETE). Là NGUỒN DUY NHẤT cho mọi màn hình/báo cáo lịch sử kho — bài học ''hai query dựng SL song song'' (MR !1345): đừng viết SELECT lịch sử kho nào khác ngoài query dựng bảng này';
COMMENT ON COLUMN ai.lich_su_kho.but_toan_id IS 'Id bút toán (stock_movements.id) — khóa chính';
COMMENT ON COLUMN ai.lich_su_kho.ngay IS 'Ngày NGHIỆP VỤ của bút toán (occurred_at, giờ VN) — KHÔNG phải ngày ghi sổ (created_at); chứng từ ghi lùi ngày vẫn hiện đúng ngày này (MR !1234)';
COMMENT ON COLUMN ai.lich_su_kho.kho_ten IS 'Tên kho (warehouses.name)';
COMMENT ON COLUMN ai.lich_su_kho.sku IS 'Mã SKU sản phẩm';
COMMENT ON COLUMN ai.lich_su_kho.ten_sp IS 'Tên sản phẩm';
COMMENT ON COLUMN ai.lich_su_kho.loai_bien_dong_text IS 'Loại biến động kho (nhãn tiếng Việt) — dịch từ stock_movements.movement_type qua ai.nhan(''loai_bien_dong_kho'', ...): Nhập kho / Xuất kho / Điều chỉnh / Chuyển kho / Bán hàng / Hoàn bán / ...';
COMMENT ON COLUMN ai.lich_su_kho.trang_thai_ton_text IS 'Trạng thái tồn tại thời điểm bút toán (nhãn tiếng Việt, ai.nhan(''trang_thai_ton'', ...)) — một lần chuyển trạng thái (reclass) sinh HAI dòng khác state nhau, cột này phân biệt dòng nào thuộc trạng thái nào';
COMMENT ON COLUMN ai.lich_su_kho.so_luong_thay_doi IS 'Số lượng thay đổi (dấu +/-) của bút toán này';
COMMENT ON COLUMN ai.lich_su_kho.ton_sau IS 'Số dư tồn sau khi áp bút toán này (stock_movements.balance_after)';
COMMENT ON COLUMN ai.lich_su_kho.loai_chung_tu_text IS 'Loại chứng từ nguồn (nhãn tiếng Việt) — dịch từ stock_movements.reference_type qua ai.nhan(''loai_chung_tu_kho'', ...): Đơn hàng / Phiếu kho / Nhóm đơn hoàn / Chuyển trạng thái tồn / ...';
COMMENT ON COLUMN ai.lich_su_kho.chung_tu_id IS 'Id NỘI BỘ của chứng từ nguồn (stock_movements.reference_id) — ý nghĩa phụ thuộc loai_chung_tu_text. KHÔNG trùng với mã phiếu hiển thị trên màn Lịch sử nhập xuất của CRM (đó là mã phiếu nghiệp vụ, không phải id); map sang mã phiếu là việc làm sau (follow-up), chưa có ở đây.';
COMMENT ON COLUMN ai.lich_su_kho.ma_don IS 'Mã đơn hàng — chỉ có khi chứng từ là đơn hàng (reference_type = ''order''), NULL cho mọi loại chứng từ khác';
COMMENT ON COLUMN ai.lich_su_kho.ghi_chu IS 'Ghi chú tự do trên bút toán (stock_movements.note) — NULL nếu rỗng';
COMMENT ON COLUMN ai.lich_su_kho.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi vào ai.lich_su_kho — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.lich_su_kho;
DROP TABLE IF EXISTS ai.ton_kho;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0010_metadata_views")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
