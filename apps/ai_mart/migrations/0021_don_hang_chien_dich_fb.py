"""Id chiến dịch / quảng cáo Facebook trên ai.don_hang — quy đơn/lead về campaign
(API Daily Report /ads, 21/09/2026). Nguồn: orders.fb_campaign_id / fb_ad_id, bóc
từ utm_id / utm_content trên url_page landing (apps/orders/meta_attribution.py).

Cột mới nối vào CUỐI bảng (loader INSERT-select không liệt kê cột — thứ tự SELECT
trong don_hang.py phải khớp, xem 0013).
"""

from django.db import migrations

DDL = """
ALTER TABLE ai.don_hang ADD COLUMN IF NOT EXISTS chien_dich_fb_id text;
ALTER TABLE ai.don_hang ADD COLUMN IF NOT EXISTS quang_cao_fb_id text;
COMMENT ON COLUMN ai.don_hang.chien_dich_fb_id IS
 'ID chiến dịch Facebook sinh ra đơn (utm_id trên URL landing = campaign.id của Meta). NULL = không xác định (đơn không phải landing, landing không gửi URL, hoặc URL không gắn utm). Nối facebook_ad_campaigns.fb_campaign_id / API Daily Report /ads campaign_id';
COMMENT ON COLUMN ai.don_hang.quang_cao_fb_id IS
 'ID quảng cáo Facebook sinh ra đơn (utm_content = ad.id của Meta). NULL = không xác định';
CREATE INDEX IF NOT EXISTS don_hang_chien_dich_fb_idx
    ON ai.don_hang (chien_dich_fb_id, ngay_tao_ngay) WHERE chien_dich_fb_id IS NOT NULL;
"""

DROP = """
DROP INDEX IF EXISTS ai.don_hang_chien_dich_fb_idx;
ALTER TABLE ai.don_hang DROP COLUMN IF EXISTS quang_cao_fb_id;
ALTER TABLE ai.don_hang DROP COLUMN IF EXISTS chien_dich_fb_id;
"""


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0020_chi_phi_qc_he_so_thang_9")]
    operations = [migrations.RunSQL(DDL, DROP)]
