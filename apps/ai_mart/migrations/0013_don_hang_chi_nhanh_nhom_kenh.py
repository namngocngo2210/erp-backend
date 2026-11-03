"""Hai cột phục vụ API Daily Report (spec 2026-09-03 §5).

Cột mới nối vào CUỐI bảng: loader INSERT-select không liệt kê cột nên thứ tự
SELECT trong don_hang.py phải khớp thứ tự cột — thêm cột = thêm ở cuối cả hai.
"""

from django.db import migrations

DDL = """
ALTER TABLE ai.don_hang ADD COLUMN IF NOT EXISTS chi_nhanh_id text;
ALTER TABLE ai.don_hang ADD COLUMN IF NOT EXISTS nhom_kenh_ban text;
COMMENT ON COLUMN ai.don_hang.chi_nhanh_id IS
 'Mã chi nhánh KiotViet của đơn (orders.branch_id) — NULL nếu đơn không gắn chi nhánh. Báo cáo tổng loại chi nhánh nội bộ 1000000139 và kho Đại Mỗ 1000000477 khỏi doanh thu';
COMMENT ON COLUMN ai.don_hang.nhom_kenh_ban IS
 'Nhóm kênh bán 5 mức của Báo cáo tổng: shopee / tiktok / facebook / web / off_other. Sàn xét TRƯỚC kênh: đơn Shopee treo trên kênh facebook-* vẫn là shopee';
CREATE INDEX IF NOT EXISTS don_hang_nhom_kenh_ban_idx ON ai.don_hang (ngay_tao_ngay, nhom_kenh_ban);
"""

DROP = """
DROP INDEX IF EXISTS ai.don_hang_nhom_kenh_ban_idx;
ALTER TABLE ai.don_hang DROP COLUMN IF EXISTS nhom_kenh_ban;
ALTER TABLE ai.don_hang DROP COLUMN IF EXISTS chi_nhanh_id;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0012_chi_phi_quang_cao")]
    operations = [migrations.RunSQL(DDL, DROP)]
