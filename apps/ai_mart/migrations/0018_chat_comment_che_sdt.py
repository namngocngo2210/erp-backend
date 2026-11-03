"""Chỉ đổi COMMENT — không đụng cấu trúc bảng nào.

Hai việc:

1. Ba cột text tự do của ai.hoi_thoai (khach_ten, nhan_tag, nhan_tag_text) nay
   ĐƯỢC CHE SĐT tại chỗ (infrastructure/sql/chat_hoi_thoai.py). COMMENT cũ mô tả
   chúng như văn bản nguyên trạng, tức nói sai với agent về dữ liệu nó đang đọc:
   một tên hồ sơ vốn là "0912345678" giờ hiện "0912***678", và agent phải biết
   đó là kết quả CHE chứ không phải số thật bị cắt.
2. COMMENT bảng ai_stg.hoi_thoai_nhan mô tả extract_payload = {raw, clean,
   rejected} mà không nói `clean` chứa NGUYÊN VĂN trích dẫn của khách (khoá
   `evidence`). Theo quy ước, `clean` là nửa "công bố được" — ghi rõ để không ai
   đổ thẳng `clean` lên một bảng có cấp quyền mà tưởng nó đã sạch PII.

Giữ nguyên hai chuỗi mà test khác đang bám: COMMENT của noi_dung/tom_tat_ai phải
còn "KHÔNG trích SĐT/địa chỉ từ cột này ra câu trả lời" (test_chat_contract),
COMMENT của don_id phải còn "KHÔNG có nghĩa là" — migration này không chạm tới
ba cột đó.
"""

from django.db import migrations

SQL = """
COMMENT ON COLUMN ai.hoi_thoai.khach_ten IS
 'Tên khách theo hồ sơ Pancake (thường là tên tài khoản mạng xã hội, KHÔNG phải tên trên đơn) — NULL nếu nguồn để trống. ĐÃ CHE SĐT: nhân viên rất hay đổi tên hồ sơ thành chính số điện thoại, nên mọi số 10 chữ số trong tên được che giữa tại chỗ (0912345678 -> 0912***678), phần chữ còn lại giữ nguyên.';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag IS
 'Mảng tên thẻ nhân viên/hệ thống gắn cho hội thoại trên Pancake — mảng rỗng nếu chưa gắn thẻ nào. ĐÃ CHE SĐT: thẻ đặt theo số điện thoại khách bị che giữa tại chỗ (0912345678 -> 0912***678).';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag_text IS
 'Các thẻ nối bằng dấu phẩy, tiện đọc/tìm — NULL nếu chưa gắn thẻ nào. ĐÃ CHE SĐT giống nhan_tag.';
COMMENT ON TABLE ai_stg.hoi_thoai_nhan IS
 'Hàng đợi + kết quả gán nhãn LLM cho hội thoại chat. extract_payload giữ nguyên hình dạng {raw, clean, rejected} của callcenter_call_records: chỉ phần clean được đổ lên ai.hoi_thoai, rejected giữ lại để biết từ điển thiếu mã nào. LƯU Ý: clean KHÔNG phải dữ liệu đã sạch PII — khoá clean.evidence chứa NGUYÊN VĂN trích dẫn lời khách (có thể có SĐT/địa chỉ), hiện không cột mart nào đọc tới. KHÔNG cấp quyền cho hermes_ro.';
"""

REVERSE = """
COMMENT ON COLUMN ai.hoi_thoai.khach_ten IS
 'Tên khách theo hồ sơ Pancake (thường là tên tài khoản mạng xã hội, KHÔNG phải tên trên đơn) — NULL nếu nguồn để trống';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag IS
 'Mảng tên thẻ nhân viên/hệ thống gắn cho hội thoại trên Pancake — mảng rỗng nếu chưa gắn thẻ nào';
COMMENT ON COLUMN ai.hoi_thoai.nhan_tag_text IS
 'Các thẻ nối bằng dấu phẩy, tiện đọc/tìm — NULL nếu chưa gắn thẻ nào';
COMMENT ON TABLE ai_stg.hoi_thoai_nhan IS
 'Hàng đợi + kết quả gán nhãn LLM cho hội thoại chat. extract_payload giữ nguyên hình dạng {raw, clean, rejected} của callcenter_call_records: chỉ phần clean được đổ lên ai.hoi_thoai, rejected giữ lại để biết từ điển thiếu mã nào. KHÔNG cấp quyền cho hermes_ro.';
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0017_hoi_thoai")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=SQL, reverse_sql=REVERSE)]
        )
    ]
