"""Báo cáo chi phí theo chiến dịch — roll-up chi tiêu grain ad theo FK chiến dịch."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.facebook_ads.campaign_report import UNKNOWN_CAMPAIGN_LABEL, aggregate_campaign_cost
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdCampaign,
    FacebookAdDailySpend,
    FacebookApp,
)
from apps.users.models import DataScope
from apps.users.tests.helpers import make_user_with_sections


class AggregateCampaignCostTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="TKQC A", account_id="9", fb_id="act_9",
        )

    def _campaign(self, fb_campaign_id, name, ad_account=None):
        if not fb_campaign_id:
            return None
        campaign, _ = FacebookAdCampaign.objects.get_or_create(
            fb_campaign_id=fb_campaign_id,
            defaults={"name": name, "ad_account": ad_account or self.account},
        )
        return campaign

    def _ad(self, fb_ad_id, campaign_id, campaign_name):
        return FacebookAd.objects.create(
            fb_ad_id=fb_ad_id, ad_account=self.account, name=f"Ad {fb_ad_id}",
            campaign=self._campaign(campaign_id, campaign_name),
        )

    def _spend(self, ad, day, amount):
        FacebookAdDailySpend.objects.create(
            ad=ad, date=day, spend=Decimal(amount), impressions=100, clicks=5,
        )

    def test_sums_ads_of_the_same_campaign_and_applies_multiplier(self):
        ad_a = self._ad("1", "c1", "CD 1")
        ad_b = self._ad("2", "c1", "CD 1")
        self._spend(ad_a, date(2026, 8, 1), "100.00")
        self._spend(ad_b, date(2026, 8, 1), "100.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)

        self.assertEqual(len(rows), 1)
        # (100 + 100) * 1.08 — nhân TRÊN TỔNG, không nhân từng dòng. Hệ số 1.08 vì
        # kỳ bắt đầu 01/08/2026, đúng mốc đổi hệ số của Facebook.
        self.assertEqual(rows[0]["cost"], "216.00")
        self.assertEqual(rows[0]["campaign_name"], "CD 1")
        self.assertEqual(rows[0]["platform"], "facebook")
        self.assertEqual(rows[0]["impressions"], 200)
        self.assertIsNone(rows[0]["revenue"])

    def test_ad_without_campaign_gets_explicit_label(self):
        ad = self._ad("3", "", "")
        self._spend(ad, date(2026, 8, 1), "50.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)

        self.assertEqual(rows[0]["campaign_name"], UNKNOWN_CAMPAIGN_LABEL)

    def test_zero_cost_keeps_two_decimals(self):
        ad = self._ad("4", "c2", "CD 2")
        self._spend(ad, date(2026, 8, 1), "0.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)

        self.assertEqual(rows[0]["cost"], "0.00")

    def test_filters_by_ad_account(self):
        other_account = FacebookAdAccount.objects.create(
            app=self.app, name="TKQC B", account_id="8", fb_id="act_8",
        )
        mine = self._ad("5", "c1", "CD 1")
        theirs = FacebookAd.objects.create(
            fb_ad_id="6", ad_account=other_account,
            campaign=self._campaign("c9", "CD 9", ad_account=other_account),
        )
        self._spend(mine, date(2026, 8, 1), "10.00")
        self._spend(theirs, date(2026, 8, 1), "999.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), [self.account.id])

        self.assertEqual([r["campaign_name"] for r in rows], ["CD 1"])

    def test_cost_applies_the_facebook_multiplier_of_the_period(self):
        """Chi phí nhân hệ số theo ngày BẮT ĐẦU kỳ (kỳ tháng 8/2026 → ×1.08).

        Bảng mốc/biên ngày được khoá ở unit test
        `apps/orders/tests/test_facebook_ads_cost_multiplier.py`; ở đây chỉ chứng
        minh báo cáo có đi qua hệ số đó.
        """
        ad = self._ad("8", "c1", "CD 1")
        self._spend(ad, date(2026, 8, 1), "100.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)

        self.assertEqual(rows[0]["cost"], "108.00")

    def test_outside_date_range_excluded(self):
        ad = self._ad("7", "c1", "CD 1")
        self._spend(ad, date(2026, 7, 31), "10.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 31), None)

        self.assertEqual(rows, [])


class CampaignReportApiTests(TestCase):
    # Dùng helper chuẩn của repo (mirror test_page_report_api.py): gán đủ
    # permission của section "ads-report" + DataScope.ALL — `User.objects.create_user`
    # trần với is_superuser=True KHÔNG đủ, vì `require_section` kiểm tra role RBAC
    # (Role.is_system), không phải cờ is_superuser của Django.
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user_with_sections("fb_campaign_ops", ["ads-report"])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_iso_dates(self):
        url = reverse("fb-campaign-cost-report")
        response = self.client.get(url, {"since": "01/08/2026", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 400)

    def test_rejects_reversed_range(self):
        url = reverse("fb-campaign-cost-report")
        response = self.client.get(url, {"since": "2026-08-07", "until": "2026-08-01"})
        self.assertEqual(response.status_code, 400)

    def test_returns_rows_and_truncated_flag(self):
        url = reverse("fb-campaign-cost-report")
        response = self.client.get(url, {"since": "2026-08-01", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["rows"], [])
        self.assertFalse(response.data["truncated"])
        self.assertEqual(response.data["dimension"], "campaign")


class CampaignReportScopeTests(TestCase):
    """NV ads phải thấy chi tiêu của TKQC được gán, kể cả khi kênh trỏ bản sao khác.

    Toàn bộ `CampaignReportApiTests` dùng user scope ALL nên nhánh lọc
    `if ad_account_ids is not None` chưa từng chạy — đúng lỗ hổng đã để lọt bug prod
    2026-08-10 (kênh map PK của app 1, chi tiêu nằm dưới PK của app 4).
    """

    def setUp(self):
        self.app1 = FacebookApp.objects.create(
            name="App 1", app_id="1", app_secret="s", access_token="t",
        )
        self.app4 = FacebookApp.objects.create(
            name="App 4", app_id="4", app_secret="s", access_token="t",
        )
        self.shell = FacebookAdAccount.objects.create(
            app=self.app1, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        self.real = FacebookAdAccount.objects.create(
            app=self.app4, name="LỢI_TK7_HKD",
            account_id="25322428374101900", fb_id="act_25322428374101900",
        )
        # Chi tiêu treo dưới bản của app 4 — bản mà kênh KHÔNG trỏ tới.
        mine = FacebookAd.objects.create(
            fb_ad_id="M1", ad_account=self.real,
            campaign=FacebookAdCampaign.objects.create(
                fb_campaign_id="c1", name="CD của tôi", ad_account=self.real,
            ),
        )
        FacebookAdDailySpend.objects.create(
            ad=mine, date=date(2026, 8, 1), spend=Decimal("100.00"),
            impressions=1000, clicks=50,
        )

        # TKQC của kênh khác — không được lộ.
        self.other_account = FacebookAdAccount.objects.create(
            app=self.app4, name="TKQC khác", account_id="999", fb_id="act_999",
        )
        theirs = FacebookAd.objects.create(
            fb_ad_id="T1", ad_account=self.other_account,
            campaign=FacebookAdCampaign.objects.create(
                fb_campaign_id="c9", name="CD người khác", ad_account=self.other_account,
            ),
        )
        FacebookAdDailySpend.objects.create(
            ad=theirs, date=date(2026, 8, 1), spend=Decimal("999.00"),
        )

        self.channel = Channel.objects.create(code="fb-loi", name="Facebook Lợi")
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.shell.id,
        )
        self.user = make_user_with_sections(
            "fb_campaign_scoped", ["ads-report"], scope=DataScope.CHANNEL,
        )
        UserChannel.objects.create(user=self.user, channel=self.channel, is_active=True)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _get(self):
        return self.client.get(
            reverse("fb-campaign-cost-report"),
            {"since": "2026-08-01", "until": "2026-08-01"},
        )

    def test_sees_spend_even_though_channel_maps_a_different_app_row(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["campaign_name"] for row in response.data["rows"]], ["CD của tôi"],
        )

    def test_does_not_leak_other_channel_spend(self):
        names = [row["campaign_name"] for row in self._get().data["rows"]]
        self.assertNotIn("CD người khác", names)

    def test_reports_real_account_count_not_duplicate_rows(self):
        # 2 dòng DB cho cùng 1 TKQC thật → phải trả 1.
        self.assertEqual(self._get().data["accessible_accounts"], 1)


class CampaignReportAccessibleAccountsTests(TestCase):
    """`accessible_accounts` = null nghĩa là KHÔNG bị lọc, khác hẳn 0."""

    def setUp(self):
        self.client = APIClient()

    def _get_as(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(
            reverse("fb-campaign-cost-report"),
            {"since": "2026-08-01", "until": "2026-08-01"},
        )

    def test_all_scope_reports_null(self):
        user = make_user_with_sections("fb_campaign_all", ["ads-report"])
        self.assertIsNone(self._get_as(user).data["accessible_accounts"])

    def test_scoped_user_without_channel_reports_zero(self):
        user = make_user_with_sections(
            "fb_campaign_nochan", ["ads-report"], scope=DataScope.CHANNEL,
        )
        self.assertEqual(self._get_as(user).data["accessible_accounts"], 0)
