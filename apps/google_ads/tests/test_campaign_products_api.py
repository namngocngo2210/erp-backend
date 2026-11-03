"""Gán chiến dịch Google Ads ↔ sản phẩm.

Cùng hình dạng payload với bản Facebook (frontend gộp 2 nguồn). Luật parser dùng
chung đã test ở apps/catalog và apps/facebook_ads — ở đây chỉ chốt phần riêng của
Google: hệ số VAT 1.08 và chiến dịch Shopping/Pmax cố ý không nhận dạng được.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.catalog.sku_text_match import assign_products_to_campaigns
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)
from apps.users.tests.helpers import make_user_with_permissions


class GoogleCampaignProductsApiTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.account = GoogleAdsAccount.objects.create(
            config=self.config, name="Delax.vn", customer_id="3000000000",
        )
        self.product = Product.objects.create(name="Máy massage bàn chân", sku="MSC06")
        self.campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.account, campaign_id="g1", name="DMG - MSC06 - Audience",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=self.campaign, date=date(2026, 8, 1), spend=Decimal("100.00"),
            impressions=10, clicks=1,
        )
        self.url = reverse("gads-campaign-products")

    def _client(self, *codes):
        client = APIClient()
        client.force_authenticate(make_user_with_permissions("u", codes))
        return client

    def test_backfill_command_google_provider(self):
        """``backfill_ads_campaign_products --provider google`` gán qua provider của google_ads."""
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(
            "backfill_ads_campaign_products", "--provider", "google", "--apply", stdout=out,
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.product_id, self.product.id)
        self.assertIn("Google Ads", out.getvalue())

        out = StringIO()
        call_command("backfill_google_campaign_products", stdout=out)  # alias cũ vẫn chạy
        self.assertIn("XEM THỬ", out.getvalue())

    def test_cost_uses_google_vat_multiplier(self):
        """1.08 (VAT Google), KHÔNG phải 1.1 của Facebook — ba tab cùng một màn hình."""
        response = self._client("ads-report:view").get(
            self.url, {"since": "2026-08-01", "until": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["rows"][0]["cost"], "108.00")

    def test_view_permission_alone_cannot_assign(self):
        response = self._client("ads-report:view").patch(
            self.url, {"id": self.campaign.id, "product_id": self.product.id}, format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_edit_permission_assigns(self):
        response = self._client("ads-report:view", "ads-report:edit").patch(
            self.url, {"id": self.campaign.id, "product_id": self.product.id}, format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.product_id, self.product.id)
        self.assertEqual(self.campaign.product_source, GoogleAdsCampaign.SOURCE_MANUAL)

    def test_shopping_and_pmax_stay_unassigned(self):
        """Chiến dịch cấp NHÓM HÀNG chạy cả feed — ép về 1 SKU là bịa số."""
        for idx, name in enumerate(("Shopping Thảm Massage", "Pmax 3", "Search-Tủ sấy quần áo")):
            GoogleAdsCampaign.objects.create(
                ad_account=self.account, campaign_id=f"grp{idx}", name=name,
            )

        assign_products_to_campaigns(GoogleAdsCampaign.objects.all())

        unassigned = GoogleAdsCampaign.objects.filter(product__isnull=True)
        self.assertEqual(unassigned.count(), 3)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.product_id, self.product.id)


class GoogleCampaignProductsAccountFilterTests(TestCase):
    """Bộ lọc TKQC — mirror bản Facebook, gom theo ``customer_id``."""

    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.delax = GoogleAdsAccount.objects.create(
            config=self.config, name="Delax.vn", customer_id="3000000000",
        )
        self.hoku = GoogleAdsAccount.objects.create(
            config=self.config, name="Hoku.vn - Search", customer_id="4000000000",
        )
        self._campaign("g1", self.delax, Decimal("100.00"))
        self._campaign("g2", self.hoku, Decimal("50.00"))
        self.url = reverse("gads-campaign-products")

    def _campaign(self, code, account, spend):
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=account, campaign_id=code, name=f"CD {code}",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=spend,
        )
        return campaign

    def _get(self, **extra):
        client = APIClient()
        client.force_authenticate(make_user_with_permissions("u", ["ads-report:view"]))
        return client.get(self.url, {"since": "2026-08-01", "until": "2026-08-31", **extra})

    def test_account_list_is_returned_sorted_by_name(self):
        response = self._get()

        self.assertEqual(
            [a["name"] for a in response.data["accounts"]], ["Delax.vn", "Hoku.vn - Search"],
        )
        self.assertEqual(response.data["accounts"][0]["platform"], "google")

    def test_filter_narrows_rows_and_cost(self):
        response = self._get(account="google:4000000000")

        self.assertEqual([r["campaign_id"] for r in response.data["rows"]], ["g2"])
        self.assertEqual(response.data["unassigned_cost"], "54.00")

    def test_picking_a_facebook_account_empties_this_platform(self):
        response = self._get(account="facebook:9")

        self.assertEqual(response.data["rows"], [])
        self.assertEqual(response.data["unassigned_cost"], "0.00")
        self.assertEqual(len(response.data["accounts"]), 2)
