"""Báo cáo chi phí Google Ads theo chiến dịch — cùng hình dạng payload với Facebook."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.google_ads.campaign_report import aggregate_campaign_cost
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)
from apps.users.models import DataScope
from apps.users.tests.helpers import make_user_with_sections


class AggregateGoogleCampaignCostTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.account = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC G", customer_id="3000000000",
        )

    def _spend(self, campaign_id, name, day, amount):
        campaign, _ = GoogleAdsCampaign.objects.get_or_create(
            ad_account=self.account, campaign_id=campaign_id, defaults={"name": name},
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=day, spend=Decimal(amount), impressions=50, clicks=3,
        )

    def test_sums_days_and_applies_google_vat_multiplier(self):
        """Hệ số phải là 1.08 (VAT Google), KHÔNG phải 1.1 của Facebook.

        Ba tab nằm trên cùng một màn hình Báo cáo ads; lệch hệ số là hai con số
        chênh 1,85% không lời giải thích.
        """
        self._spend("c1", "CD G", date(2026, 8, 1), "100.00")
        self._spend("c1", "CD G", date(2026, 8, 2), "100.00")

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 2), None)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cost"], "216.00")
        self.assertEqual(rows[0]["platform"], "google")
        self.assertEqual(rows[0]["account_name"], "TKQC G")
        self.assertEqual(rows[0]["impressions"], 100)
        self.assertIsNone(rows[0]["revenue"])

    def test_zero_cost_keeps_two_decimals(self):
        self._spend("c2", "CD 0đ", date(2026, 8, 1), "0.00")
        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), None)
        self.assertEqual(rows[0]["cost"], "0.00")

    def test_filters_by_account(self):
        other = GoogleAdsAccount.objects.create(
            config=self.config, name="TKQC khác", customer_id="4000000000",
        )
        self._spend("c1", "CD G", date(2026, 8, 1), "10.00")
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=other, campaign_id="c9", name="CD khác",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("999.00"),
        )

        rows = aggregate_campaign_cost(date(2026, 8, 1), date(2026, 8, 1), [self.account.id])

        self.assertEqual([r["campaign_name"] for r in rows], ["CD G"])


class GoogleCampaignReportApiTests(TestCase):
    # Cùng pattern với test_campaign_report_api.py phía facebook_ads: dùng helper
    # chuẩn của repo thay vì `User.objects.create_user(is_superuser=True)` — RBAC
    # ở đây kiểm tra role/permission thật, không nhìn cờ is_superuser của Django.
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user_with_sections("gads_campaign_ops", ["ads-report"])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_iso_dates(self):
        url = reverse("gads-campaign-cost-report")
        response = self.client.get(url, {"since": "hôm qua", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 400)

    def test_returns_same_payload_shape_as_facebook(self):
        url = reverse("gads-campaign-cost-report")
        response = self.client.get(url, {"since": "2026-08-01", "until": "2026-08-07"})
        self.assertEqual(response.status_code, 200)
        # FE gộp hai nguồn Facebook + Google nên hai payload phải cùng bộ khoá,
        # kể cả `accessible_accounts` mới thêm ở Task 4.
        self.assertEqual(
            sorted(response.data.keys()),
            ["accessible_accounts", "dimension", "rows", "since", "truncated", "until"],
        )
        self.assertEqual(response.data["dimension"], "campaign")


class GoogleCampaignReportScopeTests(TestCase):
    """NV ads thấy đúng chi tiêu dù kênh trỏ bản sao thuộc MCC khác."""

    def setUp(self):
        self.config_a = GoogleAdsConfig.objects.create(
            name="MCC A", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )
        self.config_b = GoogleAdsConfig.objects.create(
            name="MCC B", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="2000000000",
        )
        self.shell = GoogleAdsAccount.objects.create(
            config=self.config_a, name="Delax.vn", customer_id="3000000000",
        )
        self.real = GoogleAdsAccount.objects.create(
            config=self.config_b, name="Delax.vn", customer_id="3000000000",
        )
        campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.real, campaign_id="c1", name="CD của tôi",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=campaign, date=date(2026, 8, 1), spend=Decimal("100.00"),
            impressions=1000, clicks=50,
        )

        # TKQC của kênh khác — không được lộ (mirror bản Facebook
        # test_does_not_leak_other_channel_spend).
        self.other_account = GoogleAdsAccount.objects.create(
            config=self.config_b, name="TKQC khác", customer_id="4000000000",
        )
        other_campaign = GoogleAdsCampaign.objects.create(
            ad_account=self.other_account, campaign_id="c9", name="CD người khác",
        )
        GoogleAdsCampaignDailySpend.objects.create(
            campaign=other_campaign, date=date(2026, 8, 1), spend=Decimal("999.00"),
        )

        self.channel = Channel.objects.create(code="g-delax", name="Google Delax")
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.GOOGLE,
            account_id=self.shell.id,
        )
        self.user = make_user_with_sections(
            "gads_campaign_scoped", ["ads-report"], scope=DataScope.CHANNEL,
        )
        UserChannel.objects.create(user=self.user, channel=self.channel, is_active=True)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _get(self):
        return self.client.get(
            reverse("gads-campaign-cost-report"),
            {"since": "2026-08-01", "until": "2026-08-01"},
        )

    def test_sees_spend_even_though_channel_maps_a_different_config_row(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["campaign_name"] for row in response.data["rows"]], ["CD của tôi"],
        )
        self.assertEqual(response.data["accessible_accounts"], 1)

    def test_does_not_leak_other_channel_spend(self):
        names = [row["campaign_name"] for row in self._get().data["rows"]]
        self.assertNotIn("CD người khác", names)


class GoogleCampaignReportAccessibleAccountsTests(TestCase):
    """`accessible_accounts` = null nghĩa là KHÔNG bị lọc, khác hẳn 0.

    Mirror `CampaignReportAccessibleAccountsTests` phía facebook_ads. Quan trọng vì
    FE chỉ hiện băng cảnh báo "chưa gán TKQC nào" khi CẢ HAI nguồn (fb + google) trả
    0 — nếu Google trả `null` thay vì `0` cho user scope CHANNEL chưa gán kênh, băng
    cảnh báo tắt vĩnh viễn và bug "bảng trắng câm" quay lại mà không test nào bắt.
    """

    def setUp(self):
        self.client = APIClient()

    def _get_as(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(
            reverse("gads-campaign-cost-report"),
            {"since": "2026-08-01", "until": "2026-08-01"},
        )

    def test_all_scope_reports_null(self):
        user = make_user_with_sections("gads_campaign_all", ["ads-report"])
        self.assertIsNone(self._get_as(user).data["accessible_accounts"])

    def test_scoped_user_without_channel_reports_zero(self):
        user = make_user_with_sections(
            "gads_campaign_nochan", ["ads-report"], scope=DataScope.CHANNEL,
        )
        self.assertEqual(self._get_as(user).data["accessible_accounts"], 0)
