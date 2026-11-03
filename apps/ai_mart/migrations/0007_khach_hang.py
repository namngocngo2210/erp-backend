"""Bảng ai.khach_hang — mart nóng 1 dòng/khách hàng, aggregate từ ai.don_hang
(Sprint 2, Task 10). DDL + COMMENT tiếng Việt cho toàn bộ cột — test_contract.py
xác nhận không thiếu cột nào. Transform sinh dữ liệu nằm ở
infrastructure/sql/khach_hang.py, migration này chỉ tạo khung bảng rỗng; nạp
dữ liệu là việc của loaders.

23 cột, đúng thứ tự SELECT (INSERT theo vị trí, không liệt kê tên cột).
khoang_cach_mua_lai_trung_vi: double precision — percentile_cont() trên biểu
thức double precision (EXTRACT(EPOCH ...) / 86400.0) trả về double precision,
không phải numeric.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.khach_hang (
    khach_id bigint PRIMARY KEY,
    ma_khach text,
    ten_khach text,
    sdt_che text,
    gioi_tinh_text text,
    phan_khuc_ma text,
    phan_khuc_text text,
    la_khach_moi boolean,
    so_don_gtc int,
    so_don_tat_ca int,
    so_don_huy int,
    tong_chi_tieu numeric,
    gia_tri_don_tb numeric,
    lan_mua_dau timestamptz,
    lan_mua_cuoi timestamptz,
    so_ngay_tu_lan_mua_cuoi int,
    khoang_cach_mua_lai_trung_vi double precision,
    kenh_da_mua text[],
    tinh_da_mua text[],
    sku_hay_mua text[],
    so_cuoc_goi int,
    diem_chat_luong_tb numeric,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS khach_hang_ma_khach_idx ON ai.khach_hang (ma_khach);
CREATE INDEX IF NOT EXISTS khach_hang_lan_mua_cuoi_idx ON ai.khach_hang (lan_mua_cuoi);

COMMENT ON TABLE ai.khach_hang IS
 'Mỗi dòng = 1 khách hàng, số liệu mua tổng hợp từ ai.don_hang (chỉ đơn GTC trừ khi cột nói khác). SĐT đã che.';
COMMENT ON COLUMN ai.khach_hang.khach_id IS 'Id khách hàng trong hệ vận hành — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.khach_hang.ma_khach IS 'Mã khách hàng hiển thị trên CRM — NULL nếu mã rỗng';
COMMENT ON COLUMN ai.khach_hang.ten_khach IS 'Tên khách hàng — dữ liệu vận hành nội bộ, không thuộc nhóm PII bị chặn (SĐT/địa chỉ chi tiết)';
COMMENT ON COLUMN ai.khach_hang.sdt_che IS 'SĐT khách đã che giữa (0912***678) — không có bản đầy đủ trong schema này';
COMMENT ON COLUMN ai.khach_hang.gioi_tinh_text IS 'Nhãn tiếng Việt giới tính (Nam/Nữ) — dịch từ customers.gender, NULL nếu chưa ghi nhận';
COMMENT ON COLUMN ai.khach_hang.phan_khuc_ma IS 'Mã phân khúc khách hàng (S1-S7) theo ảnh chụp gần nhất — 46% khách chưa được phân khúc nên NULL là đúng hiện trạng, không phải thiếu dữ liệu';
COMMENT ON COLUMN ai.khach_hang.phan_khuc_text IS 'Tên phân khúc — JOIN thẳng customer_segment_categories.name (bảng danh mục động, không qua ai.tu_dien_ma)';
COMMENT ON COLUMN ai.khach_hang.la_khach_moi IS 'true nếu khách chưa từng có đơn giao thành công, theo cờ is_new_customer của hệ vận hành';
COMMENT ON COLUMN ai.khach_hang.so_don_gtc IS 'Số đơn giao thành công hợp lệ để tính doanh thu (la_don_gtc) của khách';
COMMENT ON COLUMN ai.khach_hang.so_don_tat_ca IS 'Tổng số đơn của khách trong ai.don_hang, không lọc trạng thái (đã loại đơn mirror/xoá mềm từ tầng don_hang)';
COMMENT ON COLUMN ai.khach_hang.so_don_huy IS 'Số đơn của khách có trạng thái Đã hủy (trang_thai_don_ma = 6)';
COMMENT ON COLUMN ai.khach_hang.tong_chi_tieu IS 'VND, tổng tong_sau_giam của các đơn GTC — 0 nếu khách chưa có đơn GTC nào';
COMMENT ON COLUMN ai.khach_hang.gia_tri_don_tb IS 'VND, giá trị đơn trung bình (AVG tong_sau_giam) trên các đơn GTC — NULL nếu khách chưa có đơn GTC nào';
COMMENT ON COLUMN ai.khach_hang.lan_mua_dau IS 'Thời điểm tạo đơn GTC sớm nhất của khách — NULL nếu chưa có đơn GTC nào';
COMMENT ON COLUMN ai.khach_hang.lan_mua_cuoi IS 'Thời điểm tạo đơn GTC gần nhất của khách — NULL nếu chưa có đơn GTC nào';
COMMENT ON COLUMN ai.khach_hang.so_ngay_tu_lan_mua_cuoi IS 'Số ngày đã trôi qua kể từ lan_mua_cuoi tới thời điểm đồng bộ — NULL nếu khách chưa có đơn GTC nào';
COMMENT ON COLUMN ai.khach_hang.khoang_cach_mua_lai_trung_vi IS 'Số ngày trung vị giữa hai lần mua GTC liên tiếp của khách — NULL nếu khách có dưới 2 đơn GTC';
COMMENT ON COLUMN ai.khach_hang.kenh_da_mua IS 'Mảng tên kênh bán mà khách từng mua qua (theo mọi đơn trong ai.don_hang) — mảng rỗng nếu khách chưa có đơn nào';
COMMENT ON COLUMN ai.khach_hang.tinh_da_mua IS 'Mảng tỉnh/thành giao hàng mà khách từng dùng — mảng rỗng nếu khách chưa có đơn nào';
COMMENT ON COLUMN ai.khach_hang.sku_hay_mua IS 'Mảng SKU chính (giá trị dòng cao nhất) của các đơn khách từng mua — mảng rỗng nếu khách chưa có đơn nào';
COMMENT ON COLUMN ai.khach_hang.so_cuoc_goi IS 'Tổng số cuộc gọi callcenter gắn với mọi đơn của khách';
COMMENT ON COLUMN ai.khach_hang.diem_chat_luong_tb IS 'Điểm chất lượng cuộc gọi trung bình trên các đơn của khách có chấm điểm — NULL nếu chưa có đơn nào được chấm';
COMMENT ON COLUMN ai.khach_hang.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè vào ai.khach_hang — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.khach_hang;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0006_dong_don_hang")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
