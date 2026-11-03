"""Bảng ai.don_hang — mart nóng 1 dòng/đơn hàng nội bộ (spec §3.1).

DDL + COMMENT tiếng Việt cho toàn bộ cột — test_contract.py xác nhận không
thiếu cột nào. Transform sinh dữ liệu nằm ở infrastructure/sql/don_hang.py,
migration này chỉ tạo khung bảng rỗng; nạp dữ liệu là việc của loaders.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.don_hang (
    don_id bigint PRIMARY KEY,
    ma_don text,
    ngay_tao timestamptz,
    ngay_tao_ngay date,
    ngay_chot timestamptz,
    ngay_chot_ngay date,
    trang_thai_don_ma smallint,
    trang_thai_don_text text,
    trang_thai_hoa_don_ma smallint,
    trang_thai_hoa_don_text text,
    la_don_gtc boolean NOT NULL DEFAULT false,
    loai_don_ma text, loai_don_text text,
    loai_don_koc_ma text, loai_don_koc_text text,
    nguon_don_ma text, nguon_don_text text,
    hinh_thuc_giao_ma text, hinh_thuc_giao_text text,
    phuong_thuc_tt text,
    kenh_id bigint, kenh_ten text, loai_kenh_text text,
    nhan_vien_id bigint, nhan_vien_ten text,
    khach_id bigint, ma_khach text, ten_khach text, sdt_che text,
    tinh_thanh text, quan_huyen text, la_khach_moi boolean,
    tien_hang numeric, giam_gia numeric, phi_ship numeric,
    tong_sau_giam numeric, so_luong int,
    so_dong_sp int, danh_sach_sku text[], danh_sach_sku_text text, sku_chinh text,
    trang_thai_sale_ma text, trang_thai_sale_text text,
    ly_do_khong_chot_ma text, ly_do_khong_chot_text text,
    trang_thai_giao_nghiep_vu_ma text, trang_thai_giao_nghiep_vu_text text,
    nhom_ly_do_bom_ma text, nhom_ly_do_bom_text text,
    phan_loai_khach_ma text, phan_loai_khach_text text,
    nhu_cau_ma text[], nhu_cau_text text,
    tuoi_text text, tuoi_nguon_text text,
    nghe_nghiep_ma text, nghe_nghiep_text text,
    muc_do_dau_ma text, muc_do_dau_text text,
    so_cuoc_goi int, tong_giay_goi int,
    co_transcript boolean, da_boc_ai boolean, diem_chat_luong numeric,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS don_hang_ngay_tao_ngay_idx ON ai.don_hang (ngay_tao_ngay);
CREATE INDEX IF NOT EXISTS don_hang_khach_idx ON ai.don_hang (khach_id);
CREATE INDEX IF NOT EXISTS don_hang_gtc_ngay_idx ON ai.don_hang (ngay_tao_ngay) WHERE la_don_gtc;
CREATE INDEX IF NOT EXISTS don_hang_trang_thai_idx ON ai.don_hang (trang_thai_don_text);

COMMENT ON TABLE ai.don_hang IS
 'Mỗi dòng = 1 đơn hàng nội bộ (đã loại bản sao từ đối tác foxia/cnv). Doanh thu chuẩn: SUM(tong_sau_giam) WHERE la_don_gtc.';
COMMENT ON COLUMN ai.don_hang.don_id IS 'Id đơn trong hệ vận hành — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.don_hang.ma_don IS 'Mã đơn hiển thị trên CRM, vd DL12345';
COMMENT ON COLUMN ai.don_hang.ngay_tao IS 'Thời điểm tạo đơn trong hệ vận hành (UTC) — dùng ngay_tao_ngay để nhóm theo ngày làm việc Việt Nam';
COMMENT ON COLUMN ai.don_hang.ngay_tao_ngay IS 'Ngày tạo đơn quy đổi theo múi giờ Việt Nam (UTC+7) — mốc chuẩn để nhóm báo cáo theo ngày';
COMMENT ON COLUMN ai.don_hang.ngay_chot IS 'Thời điểm sale chốt đơn. Cột nguồn mới có từ 08/2026 và CHƯA backfill — NULL ở đơn cũ KHÔNG có nghĩa là chưa chốt';
COMMENT ON COLUMN ai.don_hang.ngay_chot_ngay IS 'Ngày chốt đơn quy đổi theo múi giờ Việt Nam — cùng giới hạn dữ liệu như ngay_chot, NULL không có nghĩa là chưa chốt';
COMMENT ON COLUMN ai.don_hang.trang_thai_don_ma IS 'Mã trạng thái đơn gốc từ hệ vận hành (số) — tra nghĩa qua trang_thai_don_text hoặc ai.tu_dien_ma nhóm trang_thai_don';
COMMENT ON COLUMN ai.don_hang.trang_thai_don_text IS 'Nhãn tiếng Việt của trạng thái đơn, vd "Giao thành công" — dịch từ trang_thai_don_ma';
COMMENT ON COLUMN ai.don_hang.trang_thai_hoa_don_ma IS 'Mã trạng thái hoá đơn gốc, NULL nếu đơn chưa phát sinh hoá đơn KiotViet';
COMMENT ON COLUMN ai.don_hang.trang_thai_hoa_don_text IS 'Nhãn tiếng Việt của trạng thái hoá đơn — dịch từ trang_thai_hoa_don_ma';
COMMENT ON COLUMN ai.don_hang.la_don_gtc IS 'true = đơn giao thành công hợp lệ để tính doanh thu (đã loại hóa đơn hủy, chi nhánh loại trừ)';
COMMENT ON COLUMN ai.don_hang.loai_don_ma IS 'Mã loại đơn gốc (thường/koc/...) từ hệ vận hành';
COMMENT ON COLUMN ai.don_hang.loai_don_text IS 'Nhãn tiếng Việt của loại đơn — dịch từ loai_don_ma';
COMMENT ON COLUMN ai.don_hang.loai_don_koc_ma IS 'Mã phân loại đơn KOC (chỉ có giá trị khi loai_don_ma là koc) — NULL với đơn thường';
COMMENT ON COLUMN ai.don_hang.loai_don_koc_text IS 'Nhãn tiếng Việt của loại đơn KOC — dịch từ loai_don_koc_ma';
COMMENT ON COLUMN ai.don_hang.nguon_don_ma IS 'Mã nguồn phát sinh đơn (landing/lark/kiotviet/...) từ hệ vận hành';
COMMENT ON COLUMN ai.don_hang.nguon_don_text IS 'Nhãn tiếng Việt của nguồn đơn; nếu mã chưa có trong từ điển thì trả nguyên mã gốc thay vì NULL';
COMMENT ON COLUMN ai.don_hang.hinh_thuc_giao_ma IS 'Mã hình thức giao hàng (giao ngay/giao sau/...) từ hệ vận hành';
COMMENT ON COLUMN ai.don_hang.hinh_thuc_giao_text IS 'Nhãn tiếng Việt của hình thức giao hàng — dịch từ hinh_thuc_giao_ma';
COMMENT ON COLUMN ai.don_hang.phuong_thuc_tt IS 'Text tự do từ hệ vận hành, không phải danh mục đóng';
COMMENT ON COLUMN ai.don_hang.kenh_id IS 'Id kênh bán trong hệ vận hành — dùng đối chiếu sang các báo cáo kênh khác';
COMMENT ON COLUMN ai.don_hang.kenh_ten IS 'Tên kênh bán hiển thị trên CRM, vd "Fanpage A"';
COMMENT ON COLUMN ai.don_hang.loai_kenh_text IS 'Tên loại kênh theo nền tảng (Shopee/TikTok/Facebook/Web) — NULL nếu kênh chưa được phân loại';
COMMENT ON COLUMN ai.don_hang.nhan_vien_id IS 'Id nhân viên phụ trách đơn (sale) trong hệ vận hành';
COMMENT ON COLUMN ai.don_hang.nhan_vien_ten IS 'Họ tên nhân viên phụ trách đơn, ghép từ họ và tên — NULL nếu đơn chưa gán sale';
COMMENT ON COLUMN ai.don_hang.khach_id IS 'Id khách hàng trong hệ vận hành — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.don_hang.ma_khach IS 'Mã khách hàng hiển thị trên CRM';
COMMENT ON COLUMN ai.don_hang.ten_khach IS 'Tên khách hàng — dữ liệu vận hành nội bộ, không thuộc nhóm PII bị chặn (SĐT/địa chỉ chi tiết)';
COMMENT ON COLUMN ai.don_hang.sdt_che IS 'SĐT khách đã che giữa (0912***678) — không có bản đầy đủ trong schema này';
COMMENT ON COLUMN ai.don_hang.tinh_thanh IS 'Tỉnh/thành giao hàng — dùng phân tích địa lý ở mức thô, không phải địa chỉ chi tiết';
COMMENT ON COLUMN ai.don_hang.quan_huyen IS 'Quận/huyện giao hàng — dùng phân tích địa lý ở mức thô, không phải địa chỉ chi tiết';
COMMENT ON COLUMN ai.don_hang.la_khach_moi IS 'true nếu đây là lần mua đầu tiên của khách tại thời điểm tạo đơn, theo cờ is_new_customer của hệ vận hành';
COMMENT ON COLUMN ai.don_hang.tien_hang IS 'VND, trước giảm giá — suy từ tong_sau_giam + giam_gia';
COMMENT ON COLUMN ai.don_hang.giam_gia IS 'VND, tổng số tiền giảm giá áp dụng cho đơn';
COMMENT ON COLUMN ai.don_hang.phi_ship IS 'VND, phí vận chuyển của đơn — KHÔNG cộng vào tong_sau_giam';
COMMENT ON COLUMN ai.don_hang.tong_sau_giam IS 'VND, sau giảm giá, CHƯA gồm phí ship';
COMMENT ON COLUMN ai.don_hang.so_luong IS 'Tổng số lượng sản phẩm trên đơn theo cột quantity gốc của orders — có thể lệch so_dong_sp nếu một SKU mua nhiều cái';
COMMENT ON COLUMN ai.don_hang.so_dong_sp IS 'Số dòng sản phẩm (SKU khác nhau) trên đơn, đếm từ order_items';
COMMENT ON COLUMN ai.don_hang.danh_sach_sku IS 'Mảng các mã SKU có trên đơn — mảng rỗng nếu đơn chưa có dòng sản phẩm nào';
COMMENT ON COLUMN ai.don_hang.danh_sach_sku_text IS 'Danh sách SKU nối chuỗi, phân tách bằng dấu phẩy, tiện hiển thị nhanh — NULL nếu đơn chưa có SKU';
COMMENT ON COLUMN ai.don_hang.sku_chinh IS 'SKU có giá trị dòng (line_total) cao nhất trên đơn — đại diện cho sản phẩm chính khi đơn có nhiều SKU';
COMMENT ON COLUMN ai.don_hang.trang_thai_sale_ma IS 'Mã trạng thái chăm sóc của sale (chốt/không nghe máy lần N/...) từ order_assessments';
COMMENT ON COLUMN ai.don_hang.trang_thai_sale_text IS 'Nhãn tiếng Việt của trạng thái chăm sóc sale — dịch từ trang_thai_sale_ma';
COMMENT ON COLUMN ai.don_hang.ly_do_khong_chot_ma IS 'Mã lý do sale ghi nhận khi không chốt được đơn';
COMMENT ON COLUMN ai.don_hang.ly_do_khong_chot_text IS 'Nhãn tiếng Việt của lý do không chốt — dịch từ ly_do_khong_chot_ma';
COMMENT ON COLUMN ai.don_hang.trang_thai_giao_nghiep_vu_ma IS 'Mã trạng thái giao hàng theo góc nhìn nghiệp vụ (khác trạng thái đơn vị vận chuyển), do sale/kho cập nhật tay';
COMMENT ON COLUMN ai.don_hang.trang_thai_giao_nghiep_vu_text IS 'Nhãn tiếng Việt của trạng thái giao nghiệp vụ — dịch từ trang_thai_giao_nghiep_vu_ma';
COMMENT ON COLUMN ai.don_hang.nhom_ly_do_bom_ma IS 'Mã nhóm lý do đơn bị bom hàng (khách từ chối nhận) do sale phân loại';
COMMENT ON COLUMN ai.don_hang.nhom_ly_do_bom_text IS 'Nhãn tiếng Việt của nhóm lý do bom hàng — dịch từ nhom_ly_do_bom_ma';
COMMENT ON COLUMN ai.don_hang.phan_loai_khach_ma IS 'Mã phân loại khách hàng (tiềm năng/thân thiết/...) do sale đánh giá trong quá trình chăm sóc';
COMMENT ON COLUMN ai.don_hang.phan_loai_khach_text IS 'Nhãn tiếng Việt của phân loại khách hàng — dịch từ phan_loai_khach_ma';
COMMENT ON COLUMN ai.don_hang.nhu_cau_ma IS 'Mảng mã nhu cầu khách hàng được ghi nhận cho đơn (một đơn có thể có nhiều nhu cầu) — mảng rỗng nếu chưa chấm nhu cầu';
COMMENT ON COLUMN ai.don_hang.nhu_cau_text IS 'Danh sách tên nhu cầu khách hàng nối chuỗi bằng dấu phẩy — NULL nếu đơn chưa được chấm nhu cầu nào';
COMMENT ON COLUMN ai.don_hang.tuoi_text IS 'Nhãn nhóm tuổi khách hàng, vd "45-54" — dịch từ mã nhóm tuổi ghi trong order_assessments.age_text qua ai.tu_dien_ma nhóm nhom_tuoi';
COMMENT ON COLUMN ai.don_hang.tuoi_nguon_text IS 'Nguồn của tuoi_text: "Sale nhập" hoặc "AI bóc từ ghi âm" — AI không đè số sale nhập';
COMMENT ON COLUMN ai.don_hang.nghe_nghiep_ma IS 'Mã nghề nghiệp khách hàng do sale ghi nhận khi tư vấn';
COMMENT ON COLUMN ai.don_hang.nghe_nghiep_text IS 'Nhãn tiếng Việt của nghề nghiệp khách hàng — dịch từ nghe_nghiep_ma';
COMMENT ON COLUMN ai.don_hang.muc_do_dau_ma IS 'Mã mức độ đau/vấn đề sức khoẻ khách hàng gặp phải, do sale ghi nhận khi tư vấn';
COMMENT ON COLUMN ai.don_hang.muc_do_dau_text IS 'Nhãn tiếng Việt của mức độ đau — dịch từ muc_do_dau_ma';
COMMENT ON COLUMN ai.don_hang.so_cuoc_goi IS 'Tổng số cuộc gọi callcenter gắn với đơn (qua order_id trên callcenter_call_records)';
COMMENT ON COLUMN ai.don_hang.tong_giay_goi IS 'Tổng thời lượng đàm thoại thực tế (ACD) của các cuộc gọi thuộc đơn, tính bằng giây';
COMMENT ON COLUMN ai.don_hang.co_transcript IS 'true nếu có ít nhất một cuộc gọi thuộc đơn đã có bản ghi lời thoại (transcript) không rỗng';
COMMENT ON COLUMN ai.don_hang.da_boc_ai IS 'true nếu có ít nhất một cuộc gọi thuộc đơn đã được AI bóc tách nội dung (extract_payload khác rỗng)';
COMMENT ON COLUMN ai.don_hang.diem_chat_luong IS 'Điểm chất lượng cuộc gọi GẦN NHẤT của đơn (theo start_time), không phải điểm cao nhất hay trung bình — NULL nếu chưa chấm điểm';
COMMENT ON COLUMN ai.don_hang.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè vào ai.don_hang — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.don_hang;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0004_meta_tables")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
