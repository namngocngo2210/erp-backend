"""Bảng ai.tin_nhan — grain 1 tin nhắn chat (spec 2026-09-04 §3.2).

10 cột, ĐÚNG thứ tự SELECT của infrastructure/sql/chat_tin_nhan.py (loader
INSERT theo vị trí, không liệt kê tên cột).

Chưa partition: 769k dòng còn xa ngưỡng ~10 triệu ghi ở spec §9.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.tin_nhan (
    tin_nhan_id bigint PRIMARY KEY,
    hoi_thoai_id bigint,
    page_ten text,
    gui_luc timestamptz,
    gui_luc_ngay date,
    chieu_text text,
    loai_tin_text text,
    noi_dung text,
    co_dinh_kem boolean,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS tin_nhan_hoi_thoai_idx ON ai.tin_nhan (hoi_thoai_id);
CREATE INDEX IF NOT EXISTS tin_nhan_gui_luc_ngay_idx ON ai.tin_nhan (gui_luc_ngay);

COMMENT ON TABLE ai.tin_nhan IS
 'Mỗi dòng = 1 tin nhắn chat khách hàng (nguồn Pancake/pages.fm qua crawler desi-crawl-conversations). CHỈ chứa tin CÓ nội dung chữ — tin đính kèm thuần (ảnh/sticker) bị loại. Không có cột nhân viên: nguồn không lưu admin_id, mọi tin gửi ra đều mang id của page (spec §1 F2). RIÊNG cột noi_dung là văn bản thô CHƯA che PII — xem COMMENT cột.';
COMMENT ON COLUMN ai.tin_nhan.tin_nhan_id IS 'Id tin nhắn trong DB crawler (messages.id) — chỉ dùng đối chiếu, không hiển thị cho người dùng cuối';
COMMENT ON COLUMN ai.tin_nhan.hoi_thoai_id IS 'Id hội thoại chứa tin này — nối với ai.hoi_thoai.hoi_thoai_id';
COMMENT ON COLUMN ai.tin_nhan.page_ten IS 'Tên page/gian hàng nhận tin (Facebook page, Zalo OA, gian TikTok Shop...) — NULL nếu nguồn để trống';
COMMENT ON COLUMN ai.tin_nhan.gui_luc IS 'Thời điểm Pancake ghi nhận tin (giờ UTC) — là thời điểm NGHIỆP VỤ, không phải thời điểm crawl';
COMMENT ON COLUMN ai.tin_nhan.gui_luc_ngay IS 'Ngày gửi tin theo giờ Việt Nam (Asia/Ho_Chi_Minh) — dùng để lọc/group theo ngày';
COMMENT ON COLUMN ai.tin_nhan.chieu_text IS 'Nhãn tiếng Việt chiều tin: "Khách gửi" (direction=in) hoặc "Page gửi" (direction=out). Tra ai.tu_dien_ma nhóm chieu_tin_chat. LƯU Ý: "Page gửi" gộp cả nhân viên lẫn chatbot — dùng ai.hoi_thoai.do_bot_xu_ly để lọc.';
COMMENT ON COLUMN ai.tin_nhan.loai_tin_text IS 'Nhãn tiếng Việt loại tin: Tin nhắn (INBOX) / Bình luận (COMMENT) / Đánh giá (RATING). Tra ai.tu_dien_ma nhóm loai_tin_chat.';
COMMENT ON COLUMN ai.tin_nhan.noi_dung IS 'Nguyên văn nội dung tin nhắn — CHƯA CHE PII: khách gõ thẳng SĐT và địa chỉ vào chat. Ngoại lệ có chủ đích của luật che PII (spec §4.6), cùng chuẩn với ai.cuoc_goi.transcript, vì agent cần nguyên văn để trả lời về nội dung tư vấn. KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời.';
COMMENT ON COLUMN ai.tin_nhan.co_dinh_kem IS 'Tin có đính kèm (ảnh/video/file) hay không — đo 04/09/2026: 255.080/769.234 tin có đính kèm. Nội dung đính kèm KHÔNG được lưu.';
COMMENT ON COLUMN ai.tin_nhan.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.tin_nhan;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0015_chat_stg_nguon")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
