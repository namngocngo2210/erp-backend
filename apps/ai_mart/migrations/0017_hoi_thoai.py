"""ai.hoi_thoai (mart) + ai_stg.hoi_thoai_nhan (hàng đợi & kho nhãn LLM).

34 cột, ĐÚNG thứ tự SELECT của infrastructure/sql/chat_hoi_thoai.py.

ai_stg.hoi_thoai_nhan nằm ở ai_stg vì (1) FDW chỉ đọc được nguồn nên nhãn buộc
phải sống bên desi, (2) hermes_ro KHÔNG có quyền trên ai_stg — extract_payload
thô chứa cả phần rejected, không phải hợp đồng công bố (spec C8, §7).
"""

from django.db import migrations

STG_DDL = """
CREATE TABLE IF NOT EXISTS ai_stg.hoi_thoai_nhan (
    hoi_thoai_id bigint PRIMARY KEY,
    extract_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    extract_status text NOT NULL DEFAULT 'pending',
    extract_error text NOT NULL DEFAULT '',
    extract_attempts int NOT NULL DEFAULT 0,
    model text NOT NULL DEFAULT '',
    noi_dung_hash text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hoi_thoai_nhan_status_idx
    ON ai_stg.hoi_thoai_nhan (extract_status);
-- KHÔNG có trigger ON UPDATE cho updated_at (cố ý — bắt buộc với Task 7, không
-- phải trigger). _CANDIDATES trong infrastructure/sql/chat_hoi_thoai.py đọc
-- updated_at để biết hội thoại nào cần nạp lại ở nhịp 30 phút: MỌI writer ghi
-- vào bảng này (Task 7, backfill sau này) PHẢI tự SET updated_at = now() trong
-- câu UPDATE/upsert — quên là hội thoại đó im lặng không được đồng bộ tới khi
-- rebuild đầy đủ tiếp theo chạy.
COMMENT ON TABLE ai_stg.hoi_thoai_nhan IS
 'Hàng đợi + kết quả gán nhãn LLM cho hội thoại chat. extract_payload giữ nguyên hình dạng {raw, clean, rejected} của callcenter_call_records: chỉ phần clean được đổ lên ai.hoi_thoai, rejected giữ lại để biết từ điển thiếu mã nào. KHÔNG cấp quyền cho hermes_ro.';
"""

DDL = """
CREATE TABLE IF NOT EXISTS ai.hoi_thoai (
    hoi_thoai_id bigint PRIMARY KEY,
    pancake_hoi_thoai_id text,
    page_ten text,
    nen_tang_text text,
    bat_dau timestamptz,
    bat_dau_ngay date,
    ket_thuc timestamptz,
    ket_thuc_ngay date,
    so_tin int,
    so_tin_khach int,
    so_tin_page int,
    co_khach_tra_loi boolean,
    loai_kenh_text text,
    do_bot_xu_ly boolean,
    nhan_tag text[],
    nhan_tag_text text,
    noi_dung text,
    khach_ten text,
    sdt_che text,
    tom_tat_ai text,
    nhu_cau_ai text[],
    nhu_cau_ai_text text,
    nhom_nhu_cau_ai text[],
    ly_do_khong_mua_ma text[],
    ly_do_khong_mua_text text,
    san_pham_quan_tam text[],
    giai_doan_text text,
    muc_do_quan_tam_text text,
    khach_id bigint,
    don_id bigint,
    ma_don text,
    trang_thai_gan_nhan_text text,
    gan_nhan_luc timestamptz,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS hoi_thoai_bat_dau_ngay_idx ON ai.hoi_thoai (bat_dau_ngay);
CREATE INDEX IF NOT EXISTS hoi_thoai_don_id_idx ON ai.hoi_thoai (don_id);
CREATE INDEX IF NOT EXISTS hoi_thoai_nhu_cau_idx ON ai.hoi_thoai USING gin (nhu_cau_ai);

COMMENT ON TABLE ai.hoi_thoai IS
 'Mỗi dòng = 1 hội thoại chat với khách (nguồn Pancake/pages.fm, 6 nền tảng: Facebook, Zalo OA, Zalo cá nhân, TikTok Shop, Shopee, YouTube). Đây là bảng trả lời "khách CẦN gì" và "vì sao khách KHÔNG mua" theo lời khách nói trong chat — so trực tiếp được với ai.don_hang.ly_do_khong_chot_ma (sale ghi trên phiếu) vì dùng CHUNG bộ mã. KHÔNG quy được về nhân viên: nguồn không lưu admin_id (spec §1 F2). Phần lớn lưu lượng gửi ra là chatbot — luôn lọc do_bot_xu_ly khi đọc số. RIÊNG noi_dung và tom_tat_ai là văn bản thô CHƯA che PII — xem COMMENT từng cột.';
COMMENT ON COLUMN ai.hoi_thoai.hoi_thoai_id IS 'Id hội thoại trong DB crawler (conversations.id) — nối với ai.tin_nhan.hoi_thoai_id. Chỉ dùng đối chiếu, không hiển thị cho người dùng cuối.';
COMMENT ON COLUMN ai.hoi_thoai.pancake_hoi_thoai_id IS 'Id hội thoại phía Pancake/pages.fm — dùng để mở đúng hội thoại trên giao diện Pancake. CHỈ duy nhất trong phạm vi 1 page, KHÔNG duy nhất toàn hệ thống.';
COMMENT ON COLUMN ai.hoi_thoai.page_ten IS 'Tên page/gian hàng diễn ra hội thoại — NULL nếu nguồn để trống';
COMMENT ON COLUMN ai.hoi_thoai.nen_tang_text IS 'Nhãn tiếng Việt nền tảng chat (tra ai.tu_dien_ma nhóm nen_tang_chat): Facebook / Zalo OA / Zalo cá nhân / TikTok Shop / Shopee / YouTube — NULL nếu nền tảng lạ chưa có trong từ điển';
COMMENT ON COLUMN ai.hoi_thoai.bat_dau IS 'Thời điểm tin ĐẦU TIÊN của hội thoại (timestamptz — hiển thị theo múi giờ phiên đọc, không cố định UTC hay VN) — NULL nếu hội thoại chưa có tin nào có nội dung chữ. Muốn cột đã cố định giờ Việt Nam thì dùng bat_dau_ngay.';
COMMENT ON COLUMN ai.hoi_thoai.bat_dau_ngay IS 'Ngày bắt đầu hội thoại theo giờ Việt Nam (Asia/Ho_Chi_Minh) — dùng để lọc/group theo ngày';
COMMENT ON COLUMN ai.hoi_thoai.ket_thuc IS 'Thời điểm tin CUỐI CÙNG (timestamptz — hiển thị theo múi giờ phiên đọc) — hội thoại vẫn có thể còn tiếp diễn, đây không phải mốc "đã đóng"';
COMMENT ON COLUMN ai.hoi_thoai.ket_thuc_ngay IS 'Ngày tin cuối theo giờ Việt Nam (Asia/Ho_Chi_Minh)';
COMMENT ON COLUMN ai.hoi_thoai.so_tin IS 'Tổng số tin CÓ NỘI DUNG CHỮ trong hội thoại (tin đính kèm thuần không tính) — 0 nếu hội thoại rỗng';
COMMENT ON COLUMN ai.hoi_thoai.so_tin_khach IS 'Số tin do KHÁCH gửi — chỉ báo quan trọng nhất về "hội thoại thật": đo 04/09/2026 chỉ 18.516/94.902 hội thoại có từ 2 lượt khách nói trở lên';
COMMENT ON COLUMN ai.hoi_thoai.so_tin_page IS 'Số tin gửi ra từ page — GỘP cả nhân viên lẫn chatbot, không tách được (spec §1 F2)';
COMMENT ON COLUMN ai.hoi_thoai.co_khach_tra_loi IS 'Khách có gửi ít nhất 1 tin hay không — false nghĩa là page nhắn một chiều, KHÔNG có dữ liệu nhu cầu để đọc';
COMMENT ON COLUMN ai.hoi_thoai.loai_kenh_text IS 'Nhãn tiếng Việt loại kênh hội thoại (tra ai.tu_dien_ma nhóm loai_tin_chat): Tin nhắn (INBOX, 65.263) / Bình luận (COMMENT, 28.693) / Đánh giá (RATING, 946) — số đo 04/09/2026';
COMMENT ON COLUMN ai.hoi_thoai.do_bot_xu_ly IS 'Hội thoại được gắn thẻ chatbot (thẻ bắt đầu bằng "BOT-" hoặc "TEST ") — LUÔN lọc cột này khi thống kê nhu cầu: lưu lượng bot là lý do tin gửi ra gấp ~3,8 lần tin khách gửi';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag IS 'Mảng tên thẻ nhân viên/hệ thống gắn cho hội thoại trên Pancake — mảng rỗng nếu chưa gắn thẻ nào';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag_text IS 'Các thẻ nối bằng dấu phẩy, tiện đọc/tìm — NULL nếu chưa gắn thẻ nào';
COMMENT ON COLUMN ai.hoi_thoai.noi_dung IS 'Toàn văn hội thoại ghép theo thứ tự thời gian, mỗi dòng có tiền tố vai trò "Khách: " hoặc "Page: ". CHƯA CHE PII: khách gõ thẳng SĐT và địa chỉ vào chat. Ngoại lệ có chủ đích của luật che PII (spec §4.6), cùng chuẩn với ai.cuoc_goi.transcript. KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời. NULL nếu hội thoại chưa có tin nào có nội dung chữ.';
COMMENT ON COLUMN ai.hoi_thoai.khach_ten IS 'Tên khách theo hồ sơ Pancake (thường là tên tài khoản mạng xã hội, KHÔNG phải tên trên đơn) — NULL nếu nguồn để trống';
COMMENT ON COLUMN ai.hoi_thoai.sdt_che IS 'SĐT trong hồ sơ Pancake đã che giữa (0912***678) — CỰC KỲ THƯA: đo 04/09/2026 chỉ 113/66.277 hồ sơ khách có SĐT. NULL không có nghĩa là khách không để lại số; số thường nằm trong nội dung tin.';
COMMENT ON COLUMN ai.hoi_thoai.tom_tat_ai IS 'Tóm tắt hội thoại do AI trích (extract_payload->clean->tom_tat) — NULL nếu chưa gán nhãn. CHƯA CHE PII: bản tóm tắt có thể lặp lại SĐT/địa chỉ khách gõ trong chat — cùng ngoại lệ với noi_dung, KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời.';
COMMENT ON COLUMN ai.hoi_thoai.nhu_cau_ai IS 'Mảng MÃ loại nhu cầu do AI đọc từ hội thoại (bộ mã need_types — CÙNG bộ mã sale tick trên phiếu, tra nhãn ở ai.tu_dien_ma nhóm nhu_cau). Mảng rỗng = chưa gán nhãn HOẶC khách không nói nhu cầu nào — phân biệt bằng trang_thai_gan_nhan_text.';
COMMENT ON COLUMN ai.hoi_thoai.nhu_cau_ai_text IS 'Các nhãn nhu cầu tiếng Việt nối bằng dấu phẩy — NULL nếu không có nhu cầu nào. Mã nào chưa có trong ai.tu_dien_ma nhóm nhu_cau thì hiện NGUYÊN VĂN mã đó (không phải nhãn tiếng Việt) — dấu hiệu từ điển thiếu mã.';
COMMENT ON COLUMN ai.hoi_thoai.nhom_nhu_cau_ai IS 'Mảng MÃ nhóm nhu cầu (need_groups) suy ra từ nhu_cau_ai — tra nhãn ở ai.tu_dien_ma nhóm nhom_nhu_cau. Dùng để gom nhóm khi báo cáo cấp cao.';
COMMENT ON COLUMN ai.hoi_thoai.ly_do_khong_mua_ma IS 'Mảng MÃ lý do khách không mua theo lời khách nói trong chat — DÙNG CHUNG bộ mã với ai.don_hang.ly_do_khong_chot_ma (sale ghi trên phiếu), nên so trực tiếp được "khách nói gì" với "sale ghi gì". Tra nhãn ở ai.tu_dien_ma nhóm ly_do_khong_chot.';
COMMENT ON COLUMN ai.hoi_thoai.ly_do_khong_mua_text IS 'Các nhãn lý do không mua tiếng Việt nối bằng dấu phẩy — NULL nếu không có lý do nào. Mã nào chưa có trong ai.tu_dien_ma nhóm ly_do_khong_chot thì hiện NGUYÊN VĂN mã đó (không phải nhãn tiếng Việt) — dấu hiệu từ điển thiếu mã.';
COMMENT ON COLUMN ai.hoi_thoai.san_pham_quan_tam IS 'Mảng tên sản phẩm khách nhắc tới, TEXT TỰ DO do AI trích (không phải mã sản phẩm, KHÔNG nối được với ai.san_pham) — dùng để đọc, không dùng để join';
COMMENT ON COLUMN ai.hoi_thoai.giai_doan_text IS 'Nhãn tiếng Việt giai đoạn hội thoại do AI phân loại (tra ai.tu_dien_ma nhóm giai_doan_chat): Hỏi giá / Hỏi thông tin sản phẩm / Đang cân nhắc / Từ chối / Đã chốt / Sau bán / bảo hành / Không xác định. "Đã chốt" là NHẬN ĐỊNH CỦA AI TỪ LỜI KHÁCH, không phải đơn hàng thật — muốn biết đơn thật thì xem don_id.';
COMMENT ON COLUMN ai.hoi_thoai.muc_do_quan_tam_text IS 'Nhãn tiếng Việt mức độ quan tâm do AI đánh giá (Cao / Trung bình / Thấp) — NHẬN ĐỊNH chủ quan của mô hình, không phải chỉ số đo được';
COMMENT ON COLUMN ai.hoi_thoai.khach_id IS 'Id khách hàng CỦA ĐƠN ở cột don_id (cùng quy ước với ai.cuoc_goi.khach_id) — NULL bất cứ khi nào don_id NULL, kể cả khi khách CÓ trong CRM (khớp được qua SĐT) nhưng không có đơn hợp lệ nào (đơn xoá mềm/mirror/đã bị thay bởi bản khôi phục). Xem COMMENT don_id.';
COMMENT ON COLUMN ai.hoi_thoai.don_id IS 'Id đơn hàng GẦN NHẤT của khách khớp theo SĐT AI bóc từ nội dung chat (đơn mới nhất theo ngày tạo). ĐỘ PHỦ RẤT THẤP và là best-effort: nguồn không có khoá nối chat↔đơn, chỉ 113/66.277 hồ sơ khách có SĐT nên gần như toàn bộ phụ thuộc việc khách gõ số vào chat (spec §1 F3, C7). don_id IS NULL TUYỆT ĐỐI KHÔNG có nghĩa là "khách không mua" — chỉ có nghĩa là không nối được. Liên kết này chỉ được LÀM MỚI ở rebuild đầy đủ ban đêm, không phải nhịp đồng bộ 30 phút: khách chat hôm nay rồi chốt đơn hôm sau vẫn thấy don_id NULL cho tới lần rebuild kế tiếp.';
COMMENT ON COLUMN ai.hoi_thoai.ma_don IS 'Mã đơn hiển thị trên CRM của đơn ở cột don_id — NULL khi không khớp được đơn';
COMMENT ON COLUMN ai.hoi_thoai.trang_thai_gan_nhan_text IS 'Nhãn tiếng Việt trạng thái gán nhãn AI (tra ai.tu_dien_ma nhóm trang_thai_gan_nhan): Chưa xếp hàng / Chờ gán nhãn / Đang gán nhãn / Đã gán nhãn / Gán nhãn lỗi / Bỏ qua (quá ngắn). Chỉ dòng "Đã gán nhãn" mới được coi là đã đọc hết hội thoại — mọi cột nhãn AI khác trạng thái này đều rỗng vì CHƯA CHẠY, không phải vì khách không nói.';
COMMENT ON COLUMN ai.hoi_thoai.gan_nhan_luc IS 'Thời điểm gán nhãn AI thành công gần nhất — NULL nếu chưa gán nhãn xong';
COMMENT ON COLUMN ai.hoi_thoai.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi/ghi đè — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.hoi_thoai;
DROP TABLE IF EXISTS ai_stg.hoi_thoai_nhan;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0016_tin_nhan")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=STG_DDL + DDL, reverse_sql=DROP)]
        )
    ]
