"""Chỉ đổi COMMENT — không đụng cấu trúc bảng nào.

Ngày 11/09/2026 chủ dự án chốt: hệ số VAT/phụ phí Facebook quay lại **1.1** kể từ
01/09/2026, thôi dùng 1.08 (mức đã áp từ 01/08/2026, chốt 26/08). Tháng 8/2026 vì
vậy thành một "hố" ×1.08 kẹp giữa hai giai đoạn ×1.1 — nhìn biểu đồ chi phí FB theo
tháng sẽ thấy tháng 8 thụt xuống ~1,85% rồi bật lại, KHÔNG phải mất dữ liệu.

COMMENT là thứ duy nhất trợ lý đọc để biết cột nào nghĩa gì (`ai.huong_dan_bang`
dựng thẳng từ `pg_description`), nên luật ba giai đoạn phải nằm ở đây chứ không chỉ
trong docs/ads-facebook-cost-multiplier.md — để lại "1.08 từ 01/08/2026 trở đi" là
trợ lý sẽ giải thích sai mọi câu hỏi về chi phí FB tháng 9 trở đi.

Dữ liệu cũ trong bảng KHÔNG tự sửa theo COMMENT: mart này rebuild toàn bộ mỗi đêm
(`group="nightly"`, không nạp tăng dần) nên lần rebuild kế tiếp tự tính lại hệ số
cho mọi ngày. Không cần backfill tay.
"""

from django.db import migrations

SQL = r"""
COMMENT ON TABLE ai.chi_phi_quang_cao IS
 'Chi phí quảng cáo Facebook + Google, 1 dòng/(ngày, nền tảng, TKQC, chiến dịch). QUYẾT ĐỊNH NGHIỆP VỤ: hệ số VAT/phụ phí áp THEO NGÀY (Facebook: 1.1 trước 01/08/2026 -> 1.08 trong tháng 8/2026 -> 1.1 trở lại từ 01/09/2026; Google luôn 1.08) — CỐ Ý khác màn hình báo cáo digital, nơi hệ số Facebook chọn THEO KỲ LỌC (facebook_ads_cost_multiplier(date_from), kỳ bắc qua mốc vẫn ăn nguyên hệ số cũ cho cả kỳ). Vì vậy tổng cộng dồn của bảng này CÓ THỂ KHÁC tổng trên màn hình báo cáo cho một kỳ bắc qua mốc 01/08/2026 hoặc 01/09/2026 — đây là lệch số CÓ CHỦ ĐÍCH cần giải thích cho người hỏi, không phải lỗi dữ liệu (xem MR !1374). Tháng 8/2026 là giai đoạn ×1.08 DUY NHẤT, kẹp giữa hai giai đoạn ×1.1: chi phí FB tháng 8 thấp hơn ~1,85% so với cùng mức chi tiêu ở tháng 7 hay tháng 9, KHÔNG phải thiếu dữ liệu. dong_id sinh bằng row_number() mỗi lần rebuild đêm (mart này KHÔNG nạp tăng dần) nên KHÔNG ổn định giữa các lần rebuild — đừng dùng dong_id để đối chiếu qua nhiều lần đồng bộ, chỉ dùng để phân biệt dòng trong 1 lần đọc';
COMMENT ON COLUMN ai.chi_phi_quang_cao.he_so_vat IS
 'Hệ số VAT/phụ phí áp cho dòng này, chọn THEO NGÀY (xem COMMENT bảng) — Google luôn 1.08; Facebook 1.1 trước 01/08/2026, 1.08 cho các ngày trong tháng 8/2026, rồi 1.1 trở lại từ 01/09/2026 (chủ dự án chốt 11/09/2026)';
"""


class Migration(migrations.Migration):

    dependencies = [("ai_mart", "0019_tat_gan_nhan_ghi_chu")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(SQL, migrations.RunSQL.noop)]
        )
    ]
