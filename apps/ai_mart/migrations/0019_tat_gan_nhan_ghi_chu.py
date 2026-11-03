"""Chỉ đổi COMMENT — không đụng cấu trúc bảng nào.

Ngày 07/09/2026 chủ dự án quyết định TẮT luồng gán nhãn AI (`extract_chat_labels`,
công tắc `AI_CHAT_EXTRACT_ENABLED=0`): thay vì xử lý trước 95k hội thoại rồi mới
hỏi, trợ lý đọc thẳng `ai.hoi_thoai.noi_dung` của lát cắt đã lọc hẹp lúc có câu hỏi.

Việc đó biến MƯỜI MỘT cột nhãn thành cái bẫy số liệu: chúng còn nguyên dữ liệu của
6.229/95.051 hội thoại đã kịp gán, mà mẫu LỆCH NẶNG — hàng đợi chạy theo thứ tự
khách nhắn nhiều nhất trước nên gần như toàn bộ thuộc nhóm khách >=3 tin. Một câu
`GROUP BY ly_do_khong_mua_text` vẫn chạy trơn tru và trả về con số nghe rất thuyết
phục nhưng SAI. COMMENT là thứ duy nhất agent đọc để biết dùng cột nào
(`ai.huong_dan_bang` dựng thẳng từ `pg_description`), nên cảnh báo phải nằm ở đây
chứ không phải trong tài liệu ngoài repo.

Đổi lại, COMMENT của `noi_dung` nay nói rõ nó là NGUỒN CHÍNH để phân tích, kèm giới
hạn thực dụng ~500 kB mỗi lượt đọc (đo 07/09/2026: riêng nhóm >=3 tin của tháng
8/2026 đã là 6,1 MB / 3.371 hội thoại) và mẹo lọc `so_tin_khach >= 3` để bỏ 49.496
hội thoại khách chỉ nhắn 1-2 tin.

Giữ nguyên chuỗi mà test đang bám: COMMENT của noi_dung/tom_tat_ai vẫn chứa
"KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời" (test_chat_contract).

Migration này đã được áp tay lên prod ngày 07/09/2026 (hotfix); chạy lại là no-op
vì đặt nguyên văn cùng chuỗi.
"""

from django.db import migrations

SQL = r"""
COMMENT ON COLUMN ai.hoi_thoai.noi_dung IS
 'Toàn văn hội thoại ghép theo thứ tự thời gian, mỗi dòng có tiền tố vai trò "Khách: " hoặc "Page: ". CHƯA CHE PII: khách gõ thẳng SĐT và địa chỉ vào chat. Ngoại lệ có chủ đích của luật che PII (spec §4.6), cùng chuẩn với ai.cuoc_goi.transcript. KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời. NULL nếu hội thoại chưa có tin nào có nội dung chữ. ⭐ TỪ 07/09/2026 ĐÂY LÀ NGUỒN CHÍNH để phân tích nhu cầu và lý do khách không mua (cột nhãn AI đã ngừng cập nhật): lọc hẹp bằng SQL trước (khoảng thời gian, page_ten, so_tin_khach >= 3, don_id IS NULL...) rồi đọc thẳng văn bản và tự tổng hợp. GIỚI HẠN THỰC DỤNG ~500 kB mỗi lượt đọc, tức vài trăm hội thoại: riêng nhóm >=3 tin của tháng 8/2026 đã là 6,1 MB (3.371 hội thoại) nên phải chia nhỏ lát cắt, đừng kéo cả tháng một lượt. Hội thoại khách chỉ nhắn 1-2 tin (49.496 dòng) hầu như không có nội dung tư vấn — lọc so_tin_khach >= 3 trước khi đọc.';
COMMENT ON COLUMN ai.hoi_thoai.tom_tat_ai IS
 'Tóm tắt hội thoại do AI trích (extract_payload->clean->tom_tat) — NULL nếu chưa gán nhãn. CHƯA CHE PII: bản tóm tắt có thể lặp lại SĐT/địa chỉ khách gõ trong chat — cùng ngoại lệ với noi_dung, KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.nhu_cau_ai IS
 'Mảng MÃ loại nhu cầu do AI đọc từ hội thoại (bộ mã need_types — CÙNG bộ mã sale tick trên phiếu, tra nhãn ở ai.tu_dien_ma nhóm nhu_cau). Mảng rỗng = chưa gán nhãn HOẶC khách không nói nhu cầu nào — phân biệt bằng trang_thai_gan_nhan_text. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.nhu_cau_ai_text IS
 'Các nhãn nhu cầu tiếng Việt nối bằng dấu phẩy — NULL nếu không có nhu cầu nào. Mã nào chưa có trong ai.tu_dien_ma nhóm nhu_cau thì hiện NGUYÊN VĂN mã đó (không phải nhãn tiếng Việt) — dấu hiệu từ điển thiếu mã. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.nhom_nhu_cau_ai IS
 'Mảng MÃ nhóm nhu cầu (need_groups) suy ra từ nhu_cau_ai — tra nhãn ở ai.tu_dien_ma nhóm nhom_nhu_cau. Dùng để gom nhóm khi báo cáo cấp cao. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.ly_do_khong_mua_ma IS
 'Mảng MÃ lý do khách không mua theo lời khách nói trong chat — DÙNG CHUNG bộ mã với ai.don_hang.ly_do_khong_chot_ma (sale ghi trên phiếu), nên so trực tiếp được "khách nói gì" với "sale ghi gì". Tra nhãn ở ai.tu_dien_ma nhóm ly_do_khong_chot. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.ly_do_khong_mua_text IS
 'Các nhãn lý do không mua tiếng Việt nối bằng dấu phẩy — NULL nếu không có lý do nào. Mã nào chưa có trong ai.tu_dien_ma nhóm ly_do_khong_chot thì hiện NGUYÊN VĂN mã đó (không phải nhãn tiếng Việt) — dấu hiệu từ điển thiếu mã. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.san_pham_quan_tam IS
 'Mảng tên sản phẩm khách nhắc tới, TEXT TỰ DO do AI trích (không phải mã sản phẩm, KHÔNG nối được với ai.san_pham) — dùng để đọc, không dùng để join ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.giai_doan_text IS
 'Nhãn tiếng Việt giai đoạn hội thoại do AI phân loại (tra ai.tu_dien_ma nhóm giai_doan_chat): Hỏi giá / Hỏi thông tin sản phẩm / Đang cân nhắc / Từ chối / Đã chốt / Sau bán / bảo hành / Không xác định. "Đã chốt" là NHẬN ĐỊNH CỦA AI TỪ LỜI KHÁCH, không phải đơn hàng thật — muốn biết đơn thật thì xem don_id. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.muc_do_quan_tam_text IS
 'Nhãn tiếng Việt mức độ quan tâm do AI đánh giá (Cao / Trung bình / Thấp) — NHẬN ĐỊNH chủ quan của mô hình, không phải chỉ số đo được ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.trang_thai_gan_nhan_text IS
 'Nhãn tiếng Việt trạng thái gán nhãn AI (tra ai.tu_dien_ma nhóm trang_thai_gan_nhan): Chưa xếp hàng / Chờ gán nhãn / Đang gán nhãn / Đã gán nhãn / Gán nhãn lỗi / Bỏ qua (quá ngắn). Chỉ dòng "Đã gán nhãn" mới được coi là đã đọc hết hội thoại — mọi cột nhãn AI khác trạng thái này đều rỗng vì CHƯA CHẠY, không phải vì khách không nói. ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
COMMENT ON COLUMN ai.hoi_thoai.gan_nhan_luc IS
 'Thời điểm gán nhãn AI thành công gần nhất — NULL nếu chưa gán nhãn xong ⚠️ TỪ 07/09/2026 LUỒNG GÁN NHÃN AI ĐÃ TẮT (chủ dự án quyết định: Hermes tự đọc lúc hỏi thay vì xử lý trước). Cột này chỉ phủ 6.229/95.051 hội thoại, và mẫu LỆCH NẶNG vì hàng đợi chạy theo thứ tự khách nhắn nhiều nhất trước — gần như toàn bộ thuộc nhóm khách >=3 tin. TUYỆT ĐỐI KHÔNG dùng để đếm, xếp hạng hay so sánh theo kỳ: con số sẽ sai. Muốn phân tích nhu cầu / lý do không mua thì ĐỌC THẲNG cột noi_dung của lát cắt đã lọc hẹp.';
"""


class Migration(migrations.Migration):

    dependencies = [("ai_mart", "0018_chat_comment_che_sdt")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(SQL, migrations.RunSQL.noop)]
        )
    ]
