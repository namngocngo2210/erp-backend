"""GET /ads — từng dòng chi tiêu (ngày × nền tảng × TKQC × chiến dịch) + dòng dư mỗi TKQC.

Ba bất biến khoá ở đây:
* Σ spend_adjusted theo TKQC (mọi dòng) = by_account.cost cũ — bản sao tài khoản
  (cùng account_id, khác app) chỉ tính MỘT lần (max), cùng luật ``AdsSpendPort.total_spend``.
* Σ spend_adjusted dòng có campaign_id = by_campaign.cost cũ (FB cộng từ ad, Google Max bản sao MCC).
* Dòng dư = TKQC − Σ chiến dịch, có thể ÂM — nói ra, không giấu (cost_outside_campaigns cũ).
"""

import pytest
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookAdCampaign,
    FacebookAdDailySpend,
    FacebookApp,
)
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsAccountDailySpend,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)
from apps.users.constants import DepartmentID
from apps.users.models import Department, User

from .helpers import TOKEN, client, fetch_all, rebuild, seed_order

URL = "/api/daily-report/ads"
# 02/09/2026 nằm SAU mốc 01/09 → hệ số FB ×1.1 (không phải ×1.08 của tháng 8/2026,
# xem docs/ads-facebook-cost-multiplier.md). Mọi con số chi phí dưới đây tính theo
# hệ số đó; đổi DAY sang tháng 8 là phải tính lại toàn bộ.
DAY = date(2026, 9, 2)


# Cache report/endpoint: mỗi test một LocMemCache riêng (backend/conftest.py), không cache.clear() Redis dùng chung.
pytestmark = pytest.mark.usefixtures("isolated_cache")


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class AdsEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
        ads = User.objects.create_user(username="ads-daily", password="x")
        User.objects.filter(pk=ads.pk).update(department_id=DepartmentID.ADS)
        cls.fb_ch = Channel.objects.create(code="fb-x", name="FB X")
        cls.gg_ch = Channel.objects.create(code="gg-x", name="GG X")
        for ch in (cls.fb_ch, cls.gg_ch):
            UserChannel.objects.create(user=ads, channel=ch, is_active=True)

        app1 = FacebookApp.objects.create(name="A1", app_id="a1", app_secret="s", access_token="t")
        app2 = FacebookApp.objects.create(name="A2", app_id="a2", app_secret="s", access_token="t")
        copy1 = FacebookAdAccount.objects.create(app=app1, name="TK 111", account_id="111", fb_id="act_111")
        copy2 = FacebookAdAccount.objects.create(app=app2, name="TK 111", account_id="111", fb_id="act_111")
        loose = FacebookAdAccount.objects.create(app=app1, name="TK 222", account_id="222", fb_id="act_222")
        for acc in (copy1, copy2):
            FacebookAdAccountDailySpend.objects.create(
                ad_account=acc, date=DAY, spend=Decimal("100000"), impressions=1000, clicks=10
            )
        FacebookAdAccountDailySpend.objects.create(ad_account=loose, date=DAY, spend=Decimal("30000"))
        ChannelAdAccount.objects.create(
            channel=cls.fb_ch, provider=ChannelAdAccount.Provider.FACEBOOK, account_id=copy1.id
        )

        cfg = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        gg = GoogleAdsAccount.objects.create(config=cfg, name="Delax.vn", customer_id="9999")
        GoogleAdsAccountDailySpend.objects.create(
            ad_account=gg, date=DAY, spend=Decimal("50000"), impressions=500, clicks=25
        )
        ChannelAdAccount.objects.create(
            channel=cls.gg_ch, provider=ChannelAdAccount.Provider.GOOGLE, account_id=gg.id
        )

        # Grain chiến dịch. FB: ad unique toàn cục → CỘNG các ad của cùng chiến dịch,
        # kể cả khi ad nằm trên hai bản sao TKQC (copy1/copy2 cùng account_id 111).
        # Google: bản sao MCC lặp cùng số tiền → MAX theo (ngày, customer, campaign).
        msc06 = Product.objects.create(name="Ghế MSC06", sku="MSC06")
        camp1 = FacebookAdCampaign.objects.create(
            fb_campaign_id="c1", ad_account=copy1, name="MSC06 - Tết", product=msc06
        )
        camp2 = FacebookAdCampaign.objects.create(fb_campaign_id="c2", ad_account=loose, name="Chưa gán")
        ad1 = FacebookAd.objects.create(fb_ad_id="ad1", ad_account=copy1, campaign=camp1)
        ad2 = FacebookAd.objects.create(fb_ad_id="ad2", ad_account=copy2, campaign=camp1)
        ad3 = FacebookAd.objects.create(fb_ad_id="ad3", ad_account=loose, campaign=camp2)
        FacebookAdDailySpend.objects.create(ad=ad1, date=DAY, spend=Decimal("40000"), impressions=400, clicks=4)
        FacebookAdDailySpend.objects.create(ad=ad2, date=DAY, spend=Decimal("30000"), impressions=300, clicks=3)
        FacebookAdDailySpend.objects.create(ad=ad3, date=DAY, spend=Decimal("10000"))
        cfg2 = GoogleAdsConfig.objects.create(
            name="MCC2", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="2000000000",
        )
        gg_copy = GoogleAdsAccount.objects.create(config=cfg2, name="Delax.vn", customer_id="9999")
        for acc in (gg, gg_copy):
            camp = GoogleAdsCampaign.objects.create(ad_account=acc, campaign_id="g1", name="Search - Delax")
            GoogleAdsCampaignDailySpend.objects.create(
                campaign=camp, date=DAY, spend=Decimal("20000"), impressions=200, clicks=10
            )

        seed_order(DAY, sales_status="closed", amount=300_000, channel=cls.fb_ch, staff=ads, order_source="landing")
        seed_order(DAY, sales_status="call_back_later", amount=0, channel=cls.fb_ch, staff=ads, order_source="landing")

    def _rows(self, **params):
        return fetch_all(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), **params}, limit=2)

    def test_rows_sorted_with_remainder_first_in_each_account(self):
        rows = self._rows()
        self.assertEqual(
            [(r["platform"], r["account_id"], r["campaign_id"]) for r in rows],
            [
                ("facebook", "111", None), ("facebook", "111", "c1"),
                ("facebook", "222", None), ("facebook", "222", "c2"),
                ("google", "9999", None), ("google", "9999", "g1"),
            ],
        )
        self.assertEqual([r["is_remainder"] for r in rows], [True, False, True, False, True, False])
        self.assertEqual(rows[0]["cursor"], f"{DAY.isoformat()}|facebook|111|")
        self.assertEqual(rows[1]["cursor"], f"{DAY.isoformat()}|facebook|111|c1")

    def test_campaign_rows_match_old_by_campaign(self):
        by_id = {r["campaign_id"]: r for r in self._rows() if r["campaign_id"]}
        self.assertEqual(
            by_id["c1"],
            {
                "day": DAY.isoformat(), "platform": "facebook",
                "account_id": "111", "account_name": "TK 111", "channels": ["fb-x"],
                "campaign_id": "c1", "campaign_name": "MSC06 - Tết", "sku": "MSC06", "is_remainder": False,
                "spend": 70_000, "multiplier": 1.1, "spend_adjusted": 77_000,
                "impressions": 700, "clicks": 7, "cursor": f"{DAY.isoformat()}|facebook|111|c1",
                "leads": 0, "closed": 0, "orders": 0, "revenue": 0,
            },
        )
        # Hai bản sao MCC cùng thấy g1 → tính MỘT lần (20.000 × 1.08).
        self.assertEqual((by_id["g1"]["spend"], by_id["g1"]["spend_adjusted"], by_id["g1"]["impressions"], by_id["g1"]["clicks"]), (20_000, 21_600, 200, 10))
        self.assertIsNone(by_id["g1"]["sku"])
        self.assertEqual(by_id["g1"]["channels"], ["gg-x"])
        self.assertEqual((by_id["c2"]["spend_adjusted"], by_id["c2"]["sku"], by_id["c2"]["channels"]), (11_000, None, []))

    def test_remainder_rows_make_account_totals_match_old_by_account(self):
        rows = self._rows()
        per_account = {}
        for r in rows:
            per_account[(r["platform"], r["account_id"])] = per_account.get((r["platform"], r["account_id"]), 0) + r["spend_adjusted"]
        # by_account cũ: 111 → 110.000 (Max bản sao × 1.1), 222 → 33.000, 9999 → 54.000
        self.assertEqual(per_account, {("facebook", "111"): 110_000, ("facebook", "222"): 33_000, ("google", "9999"): 54_000})
        remainders = {r["account_id"]: r for r in rows if r["is_remainder"]}
        self.assertEqual((remainders["111"]["spend"], remainders["111"]["impressions"], remainders["111"]["clicks"]), (30_000, 300, 3))
        self.assertEqual((remainders["9999"]["spend"], remainders["9999"]["impressions"], remainders["9999"]["clicks"]), (30_000, 300, 15))
        self.assertIsNone(remainders["111"]["campaign_name"])
        # Σ dòng có campaign_id = by_campaign cũ; Σ mọi dòng − Σ chiến dịch = cost_outside_campaigns cũ (87.400)
        campaign_total = sum(r["spend_adjusted"] for r in rows if not r["is_remainder"])
        self.assertEqual(campaign_total, 77_000 + 11_000 + 21_600)
        self.assertEqual(sum(per_account.values()) - campaign_total, 87_400)

    def test_negative_remainder_is_returned_not_clamped(self):
        # Chiến dịch tiêu nhiều hơn TKQC ghi nhận (sync lệch nhịp) → dòng dư âm, nói ra không giấu.
        FacebookAdDailySpend.objects.filter(ad__fb_ad_id="ad3").update(spend=Decimal("50000"))
        rem = next(r for r in self._rows() if r["is_remainder"] and r["account_id"] == "222")
        self.assertEqual(rem["spend"], -20_000)
        self.assertEqual(rem["spend_adjusted"], -22_000)

    def test_multiplier_by_period_start_and_meta_spend_as_of(self):
        body = client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat()}).json()
        self.assertEqual({r["multiplier"] for r in body["data"] if r["platform"] == "facebook"}, {1.1})
        self.assertEqual({r["multiplier"] for r in body["data"] if r["platform"] == "google"}, {1.08})
        self.assertIn("facebook", body["meta"]["spend_as_of"])
        self.assertIn("google", body["meta"]["spend_as_of"])

    def test_cursor_walk_and_bad_cursor(self):
        first = client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), "limit": "4"}).json()
        self.assertEqual(len(first["data"]), 4)
        self.assertEqual(first["next_cursor"], first["data"][-1]["cursor"])
        rest = client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), "cursor": first["next_cursor"]}).json()
        self.assertEqual(len(rest["data"]), 2)
        self.assertIsNone(rest["next_cursor"])
        self.assertEqual(client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), "cursor": "bad"}).status_code, 400)

    def test_empty_period(self):
        body = client().get(URL, {"from": "2026-08-01", "to": "2026-08-01"}).json()
        self.assertEqual(body["data"], [])
        self.assertIsNone(body["next_cursor"])

    def test_group_by_gone(self):
        self.assertEqual(client().get(URL, {"group_by": "day"}).status_code, 400)

    def test_requires_token(self):
        from rest_framework.test import APIClient

        self.assertEqual(APIClient().get(URL).status_code, 401)


DAY2 = date(2026, 9, 3)


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class AdsAttributionTests(TestCase):
    """Đơn/lead quy về chiến dịch theo `fb_campaign_id` (utm_id trên URL landing),
    ngày = ngày TẠO lead. Định nghĩa chép nguyên từ /leads/stats và /orders/stats:
    leads = mọi dòng lead; closed = sales_status.bucket closed; orders/revenue = tập
    đơn có hoá đơn (+ Σ amounts.total)."""

    @classmethod
    def setUpTestData(cls):
        cls.fb_ch = Channel.objects.create(code="fb-attr", name="FB Attr")
        app = FacebookApp.objects.create(name="A1", app_id="a1", app_secret="s", access_token="t")
        acc = FacebookAdAccount.objects.create(app=app, name="TK 111", account_id="111", fb_id="act_111")
        FacebookAdAccountDailySpend.objects.create(ad_account=acc, date=DAY, spend=Decimal("100000"))
        ChannelAdAccount.objects.create(channel=cls.fb_ch, provider=ChannelAdAccount.Provider.FACEBOOK, account_id=acc.id)
        msc06 = Product.objects.create(name="Ghế MSC06", sku="MSC06")
        c1 = FacebookAdCampaign.objects.create(fb_campaign_id="c1", ad_account=acc, name="MSC06 - Tết", product=msc06)
        # c9 có trong danh mục nhưng KHÔNG tiêu ngày DAY → vẫn phải có dòng (spend 0) để không mất đơn.
        FacebookAdCampaign.objects.create(fb_campaign_id="c9", ad_account=acc, name="Cũ")
        ad1 = FacebookAd.objects.create(fb_ad_id="ad1", ad_account=acc, campaign=c1)
        FacebookAdDailySpend.objects.create(ad=ad1, date=DAY, spend=Decimal("40000"))

        common = dict(channel=cls.fb_ch, order_source="landing")
        # c1: 1 chốt + có hoá đơn 300k, 1 đang gọi, 1 chốt nhưng chưa lên hoá đơn.
        seed_order(DAY, sales_status="closed", status=3, invoice_status=1, amount=300_000, fb_campaign_id="c1", **common)
        seed_order(DAY, sales_status="call_back_later", amount=0, fb_campaign_id="c1", **common)
        seed_order(DAY, sales_status="closed", amount=250_000, fb_campaign_id="c1", **common)
        # c9: 1 đơn có hoá đơn 200k, campaign không tiêu tiền ngày đó.
        seed_order(DAY, sales_status="closed", status=3, invoice_status=1, amount=200_000, fb_campaign_id="c9", **common)
        # c0: campaign không có trong danh mục (chưa sync) → dòng vẫn sinh, account rỗng.
        seed_order(DAY, sales_status="closed", amount=0, fb_campaign_id="c0", **common)
        # Không nối được: landing không utm + messenger.
        seed_order(DAY, sales_status="closed", status=3, invoice_status=1, amount=999_000, **common)
        seed_order(DAY, sales_status="closed", amount=0, channel=cls.fb_ch, order_source="messenger")
        # Ngày sau: c1 có lead nhưng không tiêu → dòng spend 0 ngày DAY2.
        seed_order(DAY2, sales_status="call_back_later", amount=0, fb_campaign_id="c1", **common)
        rebuild()

    def _rows(self, d_from=DAY, d_to=DAY):
        return fetch_all(URL, {"from": d_from.isoformat(), "to": d_to.isoformat()}, limit=2)

    def test_campaign_row_carries_leads_closed_orders_revenue(self):
        c1 = next(r for r in self._rows() if r["campaign_id"] == "c1")
        self.assertEqual((c1["leads"], c1["closed"], c1["orders"], c1["revenue"]), (3, 2, 1, 300_000))
        self.assertEqual(c1["spend"], 40_000)

    def test_campaign_without_spend_still_gets_a_row(self):
        rows = self._rows()
        c9 = next(r for r in rows if r["campaign_id"] == "c9")
        self.assertEqual((c9["spend"], c9["spend_adjusted"], c9["impressions"], c9["clicks"]), (0, 0, 0, 0))
        self.assertEqual((c9["account_id"], c9["account_name"], c9["channels"], c9["campaign_name"]), ("111", "TK 111", ["fb-attr"], "Cũ"))
        self.assertFalse(c9["is_remainder"])
        self.assertEqual((c9["leads"], c9["closed"], c9["orders"], c9["revenue"]), (1, 1, 1, 200_000))
        # Dòng dư của TKQC không đổi vì dòng spend 0 không tiêu gì: 100.000 − 40.000.
        rem = next(r for r in rows if r["is_remainder"] and r["account_id"] == "111")
        self.assertEqual(rem["spend"], 60_000)
        self.assertEqual((rem["leads"], rem["closed"], rem["orders"], rem["revenue"]), (None, None, None, None))

    def test_unknown_campaign_row_has_empty_account(self):
        c0 = next(r for r in self._rows() if r["campaign_id"] == "c0")
        self.assertEqual((c0["platform"], c0["account_id"], c0["account_name"], c0["campaign_name"]), ("facebook", "", "", None))
        self.assertEqual((c0["leads"], c0["closed"], c0["orders"]), (1, 1, 0))
        self.assertEqual(c0["cursor"], f"{DAY.isoformat()}|facebook||c0")

    def test_rows_on_a_day_without_spend_and_cursor_walk(self):
        rows = self._rows(DAY, DAY2)
        day2 = [r for r in rows if r["day"] == DAY2.isoformat()]
        self.assertEqual([(r["campaign_id"], r["is_remainder"], r["leads"]) for r in day2], [("c1", False, 1)])
        keys = [tuple(r["cursor"].split("|")) for r in rows]
        self.assertEqual(keys, sorted(keys))  # keyset so sánh theo tuple, đi qua fetch_all limit=2 không mất/lặp dòng
        self.assertEqual(len(rows), 5)

    def test_meta_attribution_coverage(self):
        body = client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat()}).json()
        self.assertEqual(
            body["meta"]["attribution"],
            {"leads": 7, "leads_with_campaign": 5, "landing_leads": 6, "landing_leads_with_campaign": 5},
        )

    def test_sum_over_campaign_rows_matches_orders_stats(self):
        rows = self._rows()
        by_campaign = {r["campaign_id"]: r for r in rows if r["campaign_id"]}
        orders = fetch_all("/api/daily-report/orders/stats", {"from": DAY.isoformat(), "to": DAY.isoformat()})
        tagged = [o for o in orders if o["fb_campaign_id"]]
        self.assertEqual(sum(r["orders"] for r in by_campaign.values()), len(tagged))
        self.assertEqual(sum(r["revenue"] for r in by_campaign.values()), sum(o["amounts"]["total"] for o in tagged))
