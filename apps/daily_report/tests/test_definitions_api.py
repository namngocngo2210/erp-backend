import pytest
from django.test import TestCase, override_settings

from .helpers import TOKEN, client

URL = "/api/daily-report/definitions"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
# Cache report/endpoint: mỗi test một LocMemCache riêng (backend/conftest.py), không cache.clear() Redis dùng chung.
@pytest.mark.usefixtures("isolated_cache")
class DefinitionsApiTests(TestCase):
    def test_dictionaries_and_glossary(self):
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        order_status = {r["code"]: r for r in data["order_status"]}
        self.assertEqual(order_status[3]["label"], "Giao thành công")
        self.assertEqual(order_status[3]["bucket"], "delivered")
        self.assertEqual(order_status[10]["bucket"], "return_risk")
        self.assertEqual(order_status[6]["bucket"], "cancelled")
        sales = {r["code"]: r for r in data["sales_status"]}
        self.assertEqual(sales["no_answer_5"], {"code": "no_answer_5", "label": "Không nghe máy lần 5", "bucket": "calling"})
        self.assertEqual(sales["closed"]["bucket"], "closed")
        self.assertEqual({r["code"] for r in data["invoice_status"]}, {1, 2, 3})
        self.assertIn({"code": "landing", "label": "Landing"}, data["order_source"])
        self.assertEqual(data["channel_group"], ["shopee", "tiktok", "facebook", "web", "off_other"])
        self.assertIn("close_rate", data["metrics"])
        self.assertEqual(data["excluded_branches"], ["1000000139", "1000000477"])
        self.assertIn("timezone", resp.json()["meta"])
        self.assertIn("cancelled", data["order_buckets"])

    def test_ad_account_channels_and_multiplier_rules(self):
        from apps.channels_core.models import Channel, ChannelAdAccount
        from apps.facebook_ads.models import FacebookAdAccount, FacebookApp

        ch = Channel.objects.create(code="fb-def", name="FB Def")
        app = FacebookApp.objects.create(name="A", app_id="a", app_secret="s", access_token="t")
        acc = FacebookAdAccount.objects.create(app=app, name="TK 1", account_id="1", fb_id="act_1")
        ChannelAdAccount.objects.create(channel=ch, provider=ChannelAdAccount.Provider.FACEBOOK, account_id=acc.id)

        data = client().get(URL).json()["data"]
        self.assertIn({"platform": "facebook", "account_id": "1", "name": "TK 1", "channels": ["fb-def"]}, data["ad_account_channels"])
        fb = data["cost_multiplier"]["facebook"]
        self.assertEqual(fb["schedule"][0], {"effective_from": "2026-09-01", "multiplier": 1.1})
        self.assertEqual(fb["schedule"][1], {"effective_from": "2026-08-01", "multiplier": 1.08})
        self.assertEqual(fb["schedule"][-1], {"effective_from": None, "multiplier": 1.1})
        self.assertIn("rule", fb)
        self.assertEqual(data["cost_multiplier"]["google"]["schedule"], [{"effective_from": None, "multiplier": 1.08}])
        for key in ("rows", "bucket", "close_rate", "net_revenue", "closed_revenue", "remainder_row", "channel", "cost_multiplier"):
            self.assertIn(key, data["metrics"], key)
        for gone in ("ads_by_channel", "ads_by_account", "ads_by_campaign", "cost_outside_channels", "total_orders"):
            self.assertNotIn(gone, data["metrics"], gone)
