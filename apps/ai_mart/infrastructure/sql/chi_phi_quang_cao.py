"""Grain: (ngày x nền tảng x TKQC x chiến dịch). Hệ số VAT theo NGÀY — lệch màn
hình báo cáo (chọn theo kỳ, facebook_ads_cost_multiplier) CÓ CHỦ ĐÍCH, xem
COMMENT bảng (migration 0012, cập nhật 0020) + MR !1374.

FB: spend treo theo ad (facebook_ad_daily_spends.ad_id) -> gộp về campaign qua
facebook_ads.campaign_id -> facebook_ad_campaigns.ad_account_id -> TKQC.
Bẫy TKQC nhân dòng theo FacebookApp (MR !1256, chi phí khống x4) KHÔNG áp dụng
ở đây: fb_campaign_id và fb_ad_id UNIQUE TOÀN CỤC trên facebook_ad_campaigns/
facebook_ads (xem docstring FacebookAdCampaign) — nên một chiến dịch/ad chỉ có
đúng một dòng, dù facebook_ad_accounts có thể có bản sao theo app. Join
spend -> ad -> campaign -> account vì vậy KHÔNG nhân dòng, không cần CTE dedupe.

Google: spend treo thẳng theo campaign (google_ads_campaign_daily_spends.
campaign_id) -> google_ads_campaigns.ad_account_id -> TKQC. Không có bẫy nhân
dòng tương tự (không có khái niệm nhiều "App" như Facebook)."""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.orders.reporting.shared import (
    ADS_DAILY_COST_MULTIPLIER,
    FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE,
    GOOGLE_ADS_DAILY_COST_MULTIPLIER,
)

_G = str(GOOGLE_ADS_DAILY_COST_MULTIPLIER)

# CASE hệ số dựng TỪ bảng mốc trong shared.py (mốc mới nhất trước → khớp đúng thứ tự
# ưu tiên của SQL CASE), để thêm mốc chỉ phải sửa một chỗ. Ở đây hệ số áp THEO NGÀY
# chi tiêu, khác màn hình báo cáo (theo kỳ) — xem docstring module.
_HE_SO_CASE = "CASE WHEN nen_tang_text = 'Google' THEN {g}::numeric\n{branches}\n         ELSE {fb_base}::numeric\n    END".format(
    g=_G,
    branches="\n".join(
        f"         WHEN ngay >= DATE '{effective_date.isoformat()}' THEN {multiplier}::numeric"
        for effective_date, multiplier in FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE
    ),
    fb_base=ADS_DAILY_COST_MULTIPLIER,
)


def _select(where: str) -> str:
    return f"""
WITH fb AS (
    SELECT s.date AS ngay,
           'Facebook'::text AS nen_tang_text,
           acc.name AS tkqc_ten,
           camp.name AS chien_dich_ten,
           p.sku AS sku_gan,
           sum(s.spend) AS chi_phi_goc,
           sum(s.impressions) AS hien_thi,
           sum(s.clicks) AS luot_nhap
    FROM facebook_ad_daily_spends s
    JOIN facebook_ads fa ON fa.id = s.ad_id
    JOIN facebook_ad_campaigns camp ON camp.id = fa.campaign_id
    JOIN facebook_ad_accounts acc ON acc.id = camp.ad_account_id
    LEFT JOIN products p ON p.id = camp.product_id
    GROUP BY s.date, acc.name, camp.name, p.sku
),
gg AS (
    SELECT s.date AS ngay,
           'Google'::text AS nen_tang_text,
           acc.name AS tkqc_ten,
           camp.name AS chien_dich_ten,
           p.sku AS sku_gan,
           sum(s.spend) AS chi_phi_goc,
           sum(s.impressions) AS hien_thi,
           sum(s.clicks) AS luot_nhap
    FROM google_ads_campaign_daily_spends s
    JOIN google_ads_campaigns camp ON camp.id = s.campaign_id
    JOIN google_ads_accounts acc ON acc.id = camp.ad_account_id
    LEFT JOIN products p ON p.id = camp.product_id
    GROUP BY s.date, acc.name, camp.name, p.sku
),
hop AS (SELECT * FROM fb UNION ALL SELECT * FROM gg)
SELECT
    row_number() OVER (ORDER BY ngay, nen_tang_text, tkqc_ten, chien_dich_ten) AS dong_id,
    ngay,
    nen_tang_text,
    NULLIF(btrim(coalesce(tkqc_ten, '')), '') AS tkqc_ten,
    NULLIF(btrim(coalesce(chien_dich_ten, '')), '') AS chien_dich_ten,
    NULLIF(btrim(coalesce(sku_gan, '')), '') AS sku_gan,
    chi_phi_goc,
    {_HE_SO_CASE} AS he_so_vat,
    round(chi_phi_goc * {_HE_SO_CASE}, 0) AS chi_phi_da_ap_he_so,
    hien_thi,
    luot_nhap,
    now() AS dong_bo_luc
FROM hop
WHERE {where}
"""


MART = MartTable(
    name="ai.chi_phi_quang_cao",
    key_column="dong_id",
    source_key="dong_id",
    group="nightly",
    select_sql=_select,
    candidate_ids_sql=None,
)
