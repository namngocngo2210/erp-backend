"""ai.chi_phi_quang_cao — mart chi phí quảng cáo Facebook + Google.

Hệ số VAT áp THEO NGÀY (ngay >= 2026-08-01 -> hệ số mới) — CỐ Ý khác màn hình
báo cáo digital chọn hệ số theo KỲ lọc (facebook_ads_cost_multiplier). Xem
COMMENT bảng trong migration 0012 + MR !1374 để biết lý do lệch số.

Không có factory sẵn cho facebook_ads/google_ads (đã kiểm tra
apps/facebook_ads/tests, apps/google_ads/tests) — dựng thẳng bằng
``objects.create`` với các field bắt buộc đọc từ models.py của hai app.
"""

from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chi_phi_quang_cao import MART
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdCampaign,
    FacebookAdDailySpend,
    FacebookApp,
)
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)


def _rows(**filters):
    where = " AND ".join(f"{k} = %s" for k in filters)
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT nen_tang_text, ngay, chi_phi_goc, he_so_vat, chi_phi_da_ap_he_so"
            f" FROM ai.chi_phi_quang_cao WHERE {where}", list(filters.values()),
        )
        return cur.fetchall()


class ChiPhiQuangCaoTests(TestCase):
    def setUp(self):
        # ── Facebook: App -> AdAccount -> Campaign -> Ad (spend treo ở Ad) ──
        self.fb_app = FacebookApp.objects.create(
            name="App test", app_id="app-1", app_secret="secret", access_token="token",
        )
        self.fb_account = FacebookAdAccount.objects.create(
            app=self.fb_app, name="TKQC Test", account_id="123", fb_id="act_123",
        )
        self.fb_campaign = FacebookAdCampaign.objects.create(
            fb_campaign_id="camp-1", ad_account=self.fb_account, name="Chiến dịch Test",
        )
        self.fb_ad = FacebookAd.objects.create(
            fb_ad_id="ad-1", ad_account=self.fb_account, campaign=self.fb_campaign, name="Ad Test",
        )

        # ── Google: Config -> Account -> Campaign (spend treo ở Campaign) ──
        self.gg_config = GoogleAdsConfig.objects.create(
            name="Config Test",
            developer_token="dt",
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            login_customer_id="1112223334",
        )
        self.gg_account = GoogleAdsAccount.objects.create(
            config=self.gg_config, name="TKQC Google Test", customer_id="5556667778",
        )
        self.gg_campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.gg_account, campaign_id="gcamp-1", name="Chiến dịch Google Test",
        )

    def _mk_fb_spend(self, day, spend):
        return FacebookAdDailySpend.objects.create(ad=self.fb_ad, date=day, spend=spend)

    def _mk_gg_spend(self, day, spend):
        return GoogleAdsCampaignDailySpend.objects.create(
            campaign=self.gg_campaign, date=day, spend=spend
        )

    def test_fb_multiplier_per_day(self):
        """Ba giai đoạn: 1.1 → 1.08 (tháng 8/2026) → 1.1 trở lại từ 01/09/2026.

        Tháng 8 là một "hố" ×1.08 kẹp giữa hai giai đoạn ×1.1 — mốc 01/09 phải ăn
        TRƯỚC mốc 01/08 trong CASE, nếu xếp sai thứ tự thì tháng 9 trở đi vẫn ×1.08.
        """
        self._mk_fb_spend(date(2026, 7, 31), Decimal("100"))
        self._mk_fb_spend(date(2026, 8, 1), Decimal("100"))
        self._mk_fb_spend(date(2026, 8, 31), Decimal("100"))
        self._mk_fb_spend(date(2026, 9, 1), Decimal("100"))
        loaders.full_rebuild(MART)
        rows = {r[1]: r for r in _rows(nen_tang_text="Facebook")}
        self.assertEqual(rows[date(2026, 7, 31)][3], Decimal("1.1"))
        self.assertEqual(rows[date(2026, 7, 31)][4], Decimal("110.0"))
        self.assertEqual(rows[date(2026, 8, 1)][3], Decimal("1.08"))
        self.assertEqual(rows[date(2026, 8, 1)][4], Decimal("108.0"))
        self.assertEqual(rows[date(2026, 8, 31)][3], Decimal("1.08"))
        self.assertEqual(rows[date(2026, 8, 31)][4], Decimal("108.0"))
        self.assertEqual(rows[date(2026, 9, 1)][3], Decimal("1.1"))
        self.assertEqual(rows[date(2026, 9, 1)][4], Decimal("110.0"))

    def test_google_flat_multiplier(self):
        self._mk_gg_spend(date(2026, 8, 2), Decimal("200"))
        loaders.full_rebuild(MART)
        rows = _rows(nen_tang_text="Google")
        self.assertEqual(rows[0][3], Decimal("1.08"))
        self.assertEqual(rows[0][4], Decimal("216.0"))
