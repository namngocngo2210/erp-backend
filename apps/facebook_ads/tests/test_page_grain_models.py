"""Ràng buộc schema grain ad — unique key và quan hệ page nullable."""

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
    FacebookPage,
)


class PageGrainModelTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.page = FacebookPage.objects.create(fb_page_id="111", name="Page A")

    def test_ad_can_exist_without_page(self):
        """Ad chưa resolve được page vẫn phải lưu được — mất page còn hơn mất chi phí."""
        ad = FacebookAd.objects.create(fb_ad_id="222", ad_account=self.account, page=None)
        self.assertIsNone(ad.page_id)

    def test_deleting_page_keeps_ad(self):
        """SET_NULL: xoá page không được kéo theo chi tiêu của ad."""
        ad = FacebookAd.objects.create(fb_ad_id="333", ad_account=self.account, page=self.page)
        self.page.delete()
        ad.refresh_from_db()
        self.assertIsNone(ad.page_id)

    def test_daily_spend_unique_per_ad_and_date(self):
        ad = FacebookAd.objects.create(fb_ad_id="444", ad_account=self.account, page=self.page)
        FacebookAdDailySpend.objects.create(ad=ad, date=date(2026, 7, 1), spend="10.00")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FacebookAdDailySpend.objects.create(ad=ad, date=date(2026, 7, 1), spend="20.00")

    def test_fb_ad_id_unique_globally(self):
        FacebookAd.objects.create(fb_ad_id="555", ad_account=self.account)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FacebookAd.objects.create(fb_ad_id="555", ad_account=self.account)
