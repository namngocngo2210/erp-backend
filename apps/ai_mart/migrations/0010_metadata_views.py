"""2 view siêu dữ liệu — thuốc giải cho 'ETL trễ âm thầm' (spec §3.3): agent đọc
được độ trễ nên nói 'số liệu tính đến 14:30' thay vì tưởng là số hiện tại."""

from django.db import migrations

DDL = """
CREATE OR REPLACE VIEW ai.huong_dan_bang AS
SELECT c.relname AS ten_bang,
       obj_description(c.oid) AS mo_ta_bang,
       a.attname AS ten_cot,
       format_type(a.atttypid, a.atttypmod) AS kieu_du_lieu,
       col_description(c.oid, a.attnum) AS mo_ta_cot
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'ai' AND c.relkind IN ('r', 'v')
ORDER BY c.relname, a.attnum;
COMMENT ON VIEW ai.huong_dan_bang IS
 'Tự tra cứu: mỗi dòng = 1 cột của 1 bảng trong schema ai kèm kiểu và mô tả tiếng Việt. Đọc bảng này TRƯỚC khi viết SQL.';
COMMENT ON COLUMN ai.huong_dan_bang.ten_bang IS 'Tên bảng/view trong schema ai — không có tiền tố schema';
COMMENT ON COLUMN ai.huong_dan_bang.mo_ta_bang IS 'Mô tả bảng/view tiếng Việt — từ COMMENT ON TABLE/VIEW';
COMMENT ON COLUMN ai.huong_dan_bang.ten_cot IS 'Tên cột của bảng/view';
COMMENT ON COLUMN ai.huong_dan_bang.kieu_du_lieu IS 'Kiểu dữ liệu cột (e.g. bigint, text, timestamp with time zone)';
COMMENT ON COLUMN ai.huong_dan_bang.mo_ta_cot IS 'Mô tả cột tiếng Việt — từ COMMENT ON COLUMN, NULL nếu không có';

CREATE OR REPLACE VIEW ai.tinh_trang_du_lieu AS
SELECT DISTINCT ON (nk.ten_bang)
       nk.ten_bang,
       nk.ket_thuc AS chay_lan_cuoi,
       m.moc AS du_lieu_toi_moc,
       nk.so_dong,
       floor(EXTRACT(EPOCH FROM (now() - nk.ket_thuc)) / 60)::int AS do_tre_phut,
       CASE
           WHEN nk.trang_thai <> 'ok' THEN 'Lỗi ở lần chạy gần nhất'
           WHEN now() - nk.ket_thuc > interval '90 minutes' THEN 'Trễ — số liệu có thể cũ'
           ELSE 'Bình thường'
       END AS trang_thai_text
FROM ai_meta.nhat_ky_chay nk
LEFT JOIN ai_meta.moc_dong_bo m ON m.ten_bang = '__hot__'
WHERE nk.ket_thuc IS NOT NULL
ORDER BY nk.ten_bang, nk.ket_thuc DESC;
COMMENT ON VIEW ai.tinh_trang_du_lieu IS
 'Độ tươi của từng bảng. LUÔN kiểm tra trước khi trả lời câu hỏi về số liệu hôm nay — nếu do_tre_phut lớn, nói rõ mốc dữ liệu.';
COMMENT ON COLUMN ai.tinh_trang_du_lieu.ten_bang IS 'Tên bảng mart (e.g. ai.don_hang) — từ ai_meta.nhat_ky_chay.ten_bang';
COMMENT ON COLUMN ai.tinh_trang_du_lieu.chay_lan_cuoi IS 'Thời điểm kết thúc lần chạy ETL gần nhất';
COMMENT ON COLUMN ai.tinh_trang_du_lieu.du_lieu_toi_moc IS 'Dữ liệu tính đến mốc này (watermark nhóm nóng) — khác với chay_lan_cuoi là giờ ETL chạy; đây là mốc để nói "số liệu tính đến HH:MM"';
COMMENT ON COLUMN ai.tinh_trang_du_lieu.so_dong IS 'Số dòng được ghi trong lần chạy cuối cùng';
COMMENT ON COLUMN ai.tinh_trang_du_lieu.do_tre_phut IS 'Độ trễ tính từ khi lần chạy kết thúc tới bây giờ — tính bằng phút';
COMMENT ON COLUMN ai.tinh_trang_du_lieu.trang_thai_text IS 'Trạng thái tươi dữ liệu: "Bình thường", "Trễ — số liệu có thể cũ", hoặc "Lỗi ở lần chạy gần nhất"';
"""

DROP = """
DROP VIEW IF EXISTS ai.tinh_trang_du_lieu;
DROP VIEW IF EXISTS ai.huong_dan_bang;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0009_san_pham")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
