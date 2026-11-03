"""Xoá GoogleAdsAccount (kể cả CASCADE từ GoogleAdsConfig) phải dọn ChannelAdAccount mapping.

Thay cho FK cứng — channels_core ở tầng dưới google_ads nên integrity đảm bảo
bằng signal post_delete ở app này (xem backend/.importlinter).
"""
from django.test import TestCase

from apps.channels_core.models import Channel, ChannelAdAccount
from apps.google_ads.models import GoogleAdsAccount, GoogleAdsConfig


class GoogleAdsAccountMappingCleanupTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="Cfg",
            developer_token="dev",
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            login_customer_id="1234567890",
        )
        self.acc = GoogleAdsAccount.objects.create(
            config=self.config, name="Acc", customer_id="1112223330"
        )
        self.channel = Channel.objects.create(code="chg", name="Kênh G")
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.GOOGLE,
            account_id=self.acc.id,
        )
        # Row fb trùng giá trị account_id — KHÔNG được bị dọn nhầm.
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.acc.id,
        )

    def test_delete_account_removes_only_google_mapping(self):
        acc_id = self.acc.id
        self.acc.delete()
        self.assertFalse(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.GOOGLE, account_id=acc_id
            ).exists()
        )
        self.assertTrue(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.FACEBOOK, account_id=acc_id
            ).exists()
        )

    def test_hard_delete_config_cascades_and_cleans_mapping(self):
        self.config.delete()
        self.assertFalse(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.GOOGLE
            ).exists()
        )
