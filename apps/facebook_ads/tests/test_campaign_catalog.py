"""Danh mục chiến dịch Facebook — quy tắc upsert dùng chung 2 đường ghi ad.

Hai đường đó là sync catalog (``/ads``) và lazy fetch trong sync chi tiêu; cả hai
gọi cùng ``resolve_campaign`` nên các luật ở đây áp cho cả hai.
"""

from django.test import TestCase

from apps.facebook_ads.infrastructure.campaign_upsert import resolve_campaign
from apps.facebook_ads.models import FacebookAdAccount, FacebookAdCampaign, FacebookApp


class ResolveCampaignTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="TKQC A", account_id="9", fb_id="act_9",
        )

    def test_creates_once_and_reuses(self):
        first = resolve_campaign("c1", "CD 1", ad_account=self.account)
        second = resolve_campaign("c1", "CD 1", ad_account=self.account)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(FacebookAdCampaign.objects.count(), 1)

    def test_empty_id_returns_none(self):
        """Ad không gắn chiến dịch (ad đã xoá) không được sinh dòng rác."""
        self.assertIsNone(resolve_campaign("", "CD 1", ad_account=self.account))
        self.assertIsNone(resolve_campaign("   ", "", ad_account=self.account))
        self.assertEqual(FacebookAdCampaign.objects.count(), 0)

    def test_renamed_campaign_takes_the_new_name(self):
        resolve_campaign("c1", "Tên cũ", ad_account=self.account)
        resolve_campaign("c1", "Tên mới", ad_account=self.account)

        self.assertEqual(FacebookAdCampaign.objects.get(fb_campaign_id="c1").name, "Tên mới")

    def test_blank_name_never_wipes_an_existing_name(self):
        """Lazy fetch ad đã xoá thường không lấy được tên — ghi "" lên là mất tên thật."""
        resolve_campaign("c1", "CD 1", ad_account=self.account)
        resolve_campaign("c1", "", ad_account=self.account)

        self.assertEqual(FacebookAdCampaign.objects.get(fb_campaign_id="c1").name, "CD 1")

    def test_duplicate_ad_accounts_collapse_to_one_campaign(self):
        """Bản sao TKQC (cùng account_id thật, khác PK) quy về ĐÚNG một chiến dịch.

        Đây là lý do khoá unique đặt trên ``fb_campaign_id`` chứ không phải
        ``(TKQC, campaign_id)`` — xem lớp lỗi MR !1199. Prod 2026-08-24 có 21 ca.
        """
        # Bản sao thật trên prod nằm dưới FB App khác — unique là (app, account_id),
        # nên cùng một TKQC được hai BM cùng quản lý sẽ có hai dòng.
        other_app = FacebookApp.objects.create(
            name="App 2", app_id="2", app_secret="s", access_token="t",
        )
        clone = FacebookAdAccount.objects.create(
            app=other_app, name="TKQC A", account_id="9", fb_id="act_9",
        )
        first = resolve_campaign("c1", "CD 1", ad_account=self.account)
        second = resolve_campaign("c1", "CD 1", ad_account=clone)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(FacebookAdCampaign.objects.count(), 1)
        # TKQC không đảo qua lại giữa các bản sao mỗi lượt sync.
        self.assertEqual(second.ad_account_id, self.account.id)

    def test_fills_missing_ad_account_later(self):
        """Chiến dịch tạo từ lazy fetch (chưa rõ TKQC) được điền khi sync catalog chạy."""
        campaign = FacebookAdCampaign.objects.create(fb_campaign_id="c1", name="CD 1")

        resolve_campaign("c1", "CD 1", ad_account=self.account)

        campaign.refresh_from_db()
        self.assertEqual(campaign.ad_account_id, self.account.id)


class MigrationStateTests(TestCase):
    def test_no_pending_migrations(self):
        """Model và migration không được lệch nhau.

        0008 xếp tay lại thứ tự operations (data migration phải chạy TRƯỚC khi xoá 2
        cột cũ) nên không thể tin makemigrations tự sinh lại đúng — chốt bằng check.
        """
        from django.core.management import call_command

        call_command("makemigrations", "facebook_ads", check=True, dry_run=True, verbosity=0)
