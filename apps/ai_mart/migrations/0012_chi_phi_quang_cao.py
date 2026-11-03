"""Bảng ai.chi_phi_quang_cao — mart đêm chi phí quảng cáo Facebook + Google
(Sprint 3, Task 16). DDL + COMMENT tiếng Việt cho toàn bộ cột — test_contract.py
xác nhận không thiếu cột nào. Transform sinh dữ liệu nằm ở
infrastructure/sql/chi_phi_quang_cao.py, migration này chỉ tạo khung bảng
rỗng; nạp dữ liệu là việc của loaders.

12 cột, đúng thứ tự SELECT (INSERT theo vị trí, không liệt kê tên cột).

QUYẾT ĐỊNH NGHIỆP VỤ (nhắc lại trong COMMENT bảng, không chỉ ở đây): màn hình
báo cáo digital chọn hệ số VAT/phụ phí Facebook THEO KỲ lọc
(facebook_ads_cost_multiplier(date_from) — kỳ bắc qua mốc 01/08/2026 ăn nguyên
hệ số cũ 1.1 cho cả kỳ). Bảng mart này ở grain NGÀY, không có khái niệm "kỳ",
nên áp hệ số THEO NGÀY: ngay >= 2026-08-01 -> 1.08, trước đó -> 1.1. Hệ quả:
tổng cộng dồn của mart có thể KHÁC tổng trên màn hình báo cáo cho một kỳ bắc
qua mốc 01/08/2026 — đây là lệch số CÓ CHỦ ĐÍCH, không phải bug (xem MR !1374).
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.chi_phi_quang_cao (
    dong_id bigint PRIMARY KEY,
    ngay date,
    nen_tang_text text,
    tkqc_ten text,
    chien_dich_ten text,
    sku_gan text,
    chi_phi_goc numeric,
    he_so_vat numeric,
    chi_phi_da_ap_he_so numeric,
    hien_thi bigint,
    luot_nhap bigint,
    dong_bo_luc timestamptz
);
CREATE INDEX IF NOT EXISTS chi_phi_qc_ngay_idx ON ai.chi_phi_quang_cao (ngay);
CREATE INDEX IF NOT EXISTS chi_phi_qc_nen_tang_ngay_idx
    ON ai.chi_phi_quang_cao (nen_tang_text, ngay);

COMMENT ON TABLE ai.chi_phi_quang_cao IS
 'Chi phí quảng cáo Facebook + Google, 1 dòng/(ngày, nền tảng, TKQC, chiến dịch). QUYẾT ĐỊNH NGHIỆP VỤ: hệ số VAT/phụ phí áp THEO NGÀY (ngay >= 2026-08-01 -> 1.08, trước đó -> 1.1) — CỐ Ý khác màn hình báo cáo digital, nơi hệ số Facebook chọn THEO KỲ LỌC (facebook_ads_cost_multiplier(date_from), kỳ bắc qua mốc vẫn ăn nguyên hệ số cũ). Vì vậy tổng cộng dồn của bảng này CÓ THỂ KHÁC tổng trên màn hình báo cáo cho một kỳ bắc qua 01/08/2026 — đây là lệch số CÓ CHỦ ĐÍCH cần giải thích cho người hỏi, không phải lỗi dữ liệu (xem MR !1374). dong_id sinh bằng row_number() mỗi lần rebuild đêm (mart này KHÔNG nạp tăng dần) nên KHÔNG ổn định giữa các lần rebuild — đừng dùng dong_id để đối chiếu qua nhiều lần đồng bộ, chỉ dùng để phân biệt dòng trong 1 lần đọc';
COMMENT ON COLUMN ai.chi_phi_quang_cao.dong_id IS 'Số thứ tự dòng (row_number(), sinh lại mỗi lần rebuild đêm) — khóa chính, KHÔNG ổn định giữa các lần rebuild';
COMMENT ON COLUMN ai.chi_phi_quang_cao.ngay IS 'Ngày chi tiêu (grain ngày, giờ nền tảng quảng cáo trả về)';
COMMENT ON COLUMN ai.chi_phi_quang_cao.nen_tang_text IS 'Nền tảng quảng cáo: Facebook hoặc Google';
COMMENT ON COLUMN ai.chi_phi_quang_cao.tkqc_ten IS 'Tên tài khoản quảng cáo (TKQC) — facebook_ad_accounts.name hoặc google_ads_accounts.name, NULL nếu tên rỗng';
COMMENT ON COLUMN ai.chi_phi_quang_cao.chien_dich_ten IS 'Tên chiến dịch — facebook_ad_campaigns.name hoặc google_ads_campaigns.name, NULL nếu tên rỗng';
COMMENT ON COLUMN ai.chi_phi_quang_cao.sku_gan IS 'SKU sản phẩm được gán cho chiến dịch (kết quả gán chiến dịch<->sản phẩm, MR !1353) — NULL nếu chiến dịch chưa gán sản phẩm nào, chi phí vẫn tính vào tổng, không bị giấu đi';
COMMENT ON COLUMN ai.chi_phi_quang_cao.chi_phi_goc IS 'Chi phí gốc do nền tảng báo cáo (spend), CHƯA áp hệ số VAT/phụ phí';
COMMENT ON COLUMN ai.chi_phi_quang_cao.he_so_vat IS 'Hệ số VAT/phụ phí áp cho dòng này, chọn THEO NGÀY (xem COMMENT bảng) — Google luôn 1.08; Facebook 1.1 trước 01/08/2026, 1.08 từ 01/08/2026 trở đi';
COMMENT ON COLUMN ai.chi_phi_quang_cao.chi_phi_da_ap_he_so IS 'Chi phí sau khi nhân he_so_vat, làm tròn về số nguyên (round(..., 0))';
COMMENT ON COLUMN ai.chi_phi_quang_cao.hien_thi IS 'Tổng lượt hiển thị (impressions) trong ngày, cộng dồn theo (TKQC, chiến dịch)';
COMMENT ON COLUMN ai.chi_phi_quang_cao.luot_nhap IS 'Tổng lượt nhấp (clicks) trong ngày, cộng dồn theo (TKQC, chiến dịch)';
COMMENT ON COLUMN ai.chi_phi_quang_cao.dong_bo_luc IS 'Thời điểm dòng này được ETL ghi vào ai.chi_phi_quang_cao — dùng debug độ trễ đồng bộ, không phải thời điểm nghiệp vụ';
"""

DROP = """
DROP TABLE IF EXISTS ai.chi_phi_quang_cao;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0011_kho")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=DDL, reverse_sql=DROP)]
        )
    ]
