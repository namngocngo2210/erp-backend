"""Xoá FacebookAdAccount (kể cả CASCADE từ FacebookApp) phải dọn ChannelAdAccount mapping.

Thay cho FK cứng — channels_core ở tầng dưới facebook_ads nên integrity đảm bảo
bằng signal post_delete ở app này (xem backend/.importlinter).
"""
from django.test import TestCase

from apps.channels_core.models import Channel, ChannelAdAccount
from apps.facebook_ads.models import FacebookAdAccount, FacebookApp


class FacebookAdAccountMappingCleanupTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t"
        )
        self.acc1 = FacebookAdAccount.objects.create(
            app=self.app, name="Acc1", account_id="111", fb_id="act_111"
        )
        self.acc2 = FacebookAdAccount.objects.create(
            app=self.app, name="Acc2", account_id="222", fb_id="act_222"
        )
        self.channel = Channel.objects.create(code="ch", name="Kênh")
        for pos, acc in enumerate([self.acc1, self.acc2]):
            ChannelAdAccount.objects.create(
                channel=self.channel,
                provider=ChannelAdAccount.Provider.FACEBOOK,
                account_id=acc.id,
                position=pos,
            )
        # Row google trùng giá trị account_id với acc1 — KHÔNG được bị dọn nhầm.
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.GOOGLE,
            account_id=self.acc1.id,
        )

    def test_delete_account_removes_only_its_fb_mapping(self):
        acc1_id = self.acc1.id
        self.acc1.delete()
        self.assertFalse(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.FACEBOOK, account_id=acc1_id
            ).exists()
        )
        # Mapping fb của acc2 và mapping google (trùng số id) vẫn còn.
        self.assertTrue(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.FACEBOOK, account_id=self.acc2.id
            ).exists()
        )
        self.assertTrue(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.GOOGLE, account_id=acc1_id
            ).exists()
        )
        self.assertEqual(self.channel.fb_ads_accounts, [self.acc2.id])

    def test_hard_delete_app_cascades_and_cleans_all_fb_mappings(self):
        self.app.delete()
        self.assertFalse(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.FACEBOOK
            ).exists()
        )
        self.assertEqual(
            ChannelAdAccount.objects.filter(
                provider=ChannelAdAccount.Provider.GOOGLE
            ).count(),
            1,
        )
