"""Bảng ai.cuoc_goi — mart nóng 1 dòng/cuộc gọi callcenter (Sprint 2, Task 11).
DDL + COMMENT tiếng Việt cho toàn bộ cột — test_contract.py xác nhận không
thiếu cột nào. Transform sinh dữ liệu nằm ở infrastructure/sql/cuoc_goi.py,
migration này chỉ tạo khung bảng rỗng; nạp dữ liệu là việc của loaders.

17 cột, đúng thứ tự SELECT (INSERT theo vị trí, không liệt kê tên cột).
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.cuoc_goi (
    cuoc_goi_id bigint PRIMARY KEY,
    don_id bigint,
    ma_don text,
    khach_id bigint,
    bat_dau timestamptz,
    bat_dau_ngay date,
    thoi_luong_giay int,
    loai_cuoc_goi_ma text,
    loai_cuoc_goi_text text,
    agent_ten text,
    trang_thai_boc_bang_text text,
    transcript text,
    tom_tat_ai text,
    nhu_cau_ai text[],
    diem_chat_luong numeric,
    sdt_che text,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS cuoc_goi_don_id_idx ON ai.cuoc_goi (don_id);
CREATE INDEX IF NOT EXISTS cuoc_goi_bat_dau_ngay_idx ON ai.cuoc_goi (bat_dau_ngay);

COMMENT ON TABLE ai.cuoc_goi IS
 'Mỗi dòng = 1 cuộc gọi callcenter (nguồn Zenify) — TẤT CẢ cuộc gọi ở đây đều là cuộc gọi BÁN HÀNG, tên hàng đợi HAPPYCALL của Zenify không được lưu trong DB (spec §3.1). SĐT ở cột sdt_che đã che; RIÊNG transcript/tom_tat_ai là văn bản thô CHƯA che — xem COMMENT từng cột.';
COMMENT ON COLUMN ai.cuoc_goi.cuoc_goi_id IS 'Id cuộc gọi trong hệ vận hành (callcenter_call_records.id) — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.cuoc_goi.don_id IS 'Id đơn hàng gắn với cuộc gọi — NULL nếu cuộc gọi chưa khớp được đơn nào (SĐT sàn che, hoặc chưa match)';
COMMENT ON COLUMN ai.cuoc_goi.ma_don IS 'Mã đơn hàng hiển thị trên CRM của đơn gắn với cuộc gọi — NULL nếu chưa khớp đơn hoặc mã rỗng';
COMMENT ON COLUMN ai.cuoc_goi.khach_id IS 'Id khách hàng của đơn gắn với cuộc gọi — NULL nếu chưa khớp đơn';
COMMENT ON COLUMN ai.cuoc_goi.bat_dau IS 'Thời điểm bắt đầu cuộc gọi (giờ UTC lưu trong hệ vận hành)';
COMMENT ON COLUMN ai.cuoc_goi.bat_dau_ngay IS 'Ngày bắt đầu cuộc gọi theo giờ Việt Nam (Asia/Ho_Chi_Minh) — dùng để lọc/group theo ngày';
COMMENT ON COLUMN ai.cuoc_goi.thoi_luong_giay IS 'Thời lượng đàm thoại (ACD time), đơn vị giây';
COMMENT ON COLUMN ai.cuoc_goi.loai_cuoc_goi_ma IS 'Mã chiều gọi thô từ Zenify (call_type) — NULL nếu rỗng';
COMMENT ON COLUMN ai.cuoc_goi.loai_cuoc_goi_text IS 'Nhãn tiếng Việt chiều gọi: "Gọi ra" nếu mã = ''1'', "Gọi vào" cho mọi mã khác không rỗng, NULL nếu mã rỗng';
COMMENT ON COLUMN ai.cuoc_goi.agent_ten IS 'Tên agent tổng đài — phần lớn RỖNG trên prod (hạ tầng Zenify chỉ trả 4 agent_id), NULL không có nghĩa là thiếu sót của đơn';
COMMENT ON COLUMN ai.cuoc_goi.trang_thai_boc_bang_text IS 'Nhãn tiếng Việt trạng thái transcribe (tra ai.tu_dien_ma nhóm trang_thai_boc_bang) — NULL nếu mã không nhận diện được';
COMMENT ON COLUMN ai.cuoc_goi.transcript IS 'Toàn văn transcript cuộc gọi — CHƯA CHE PII: khách thường đọc SĐT/địa chỉ thành tiếng. Ngoại lệ có chủ đích của luật che PII (spec §4.6) vì agent cần nguyên văn, cùng chuẩn API transcript MR !1377. KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời.';
COMMENT ON COLUMN ai.cuoc_goi.tom_tat_ai IS 'Tóm tắt cuộc gọi do AI trích xuất (extract_payload->clean->call_summary) — NULL nếu chưa extract hoặc rỗng. CHƯA CHE PII: bản tóm tắt AI có thể lặp lại SĐT/địa chỉ khách đọc trong cuộc gọi — cùng ngoại lệ với transcript, không trích PII ra câu trả lời.';
COMMENT ON COLUMN ai.cuoc_goi.nhu_cau_ai IS 'Mảng mã loại nhu cầu do AI trích xuất (extract_payload->clean->need_type_codes) — mảng rỗng nếu chưa extract hoặc không có nhu cầu nào';
COMMENT ON COLUMN ai.cuoc_goi.diem_chat_luong IS 'Điểm chất lượng cuộc gọi (bản chấm mới nhất theo created_at) — NULL nếu cuộc gọi chưa được chấm';
COMMENT ON COLUMN ai.cuoc_goi.sdt_che IS 'SĐT người gọi đã che giữa (0912***678), ưu tiên caller_normalized rồi mới tới caller thô — không có bản đầy đủ trong schema này';
COMMENT ON COLUMN ai.cuoc_goi.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè vào ai.cuoc_goi — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.cuoc_goi;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0007_khach_hang")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
