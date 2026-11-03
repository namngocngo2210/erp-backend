"""Bản Google của phạm vi TKQC — xem apps/facebook_ads/tests/test_ad_account_scope.py.

`GoogleAdsAccount` unique theo `(config, customer_id)` nên cùng một tài khoản thật
được hai MCC quét sẽ thành hai dòng, đúng lớp lỗi của bản Facebook. Prod 2026-08-19
CHƯA dính (`google_ads_accounts` mới 2 dòng / 2 customer_id) nhưng job sync lặp mọi
``GoogleAdsConfig``, chỉ cần thêm một MCC cùng thấy một customer là lỗi ×2 xuất
hiện y hệt — chặn trước thay vì chờ nó nổ.

Thân test dùng chung với bản Facebook qua mixin ở
`apps/channels_core/tests/ad_account_scope_mixins.py`; file này chỉ dựng dữ liệu Google.
"""

from decimal import Decimal

from django.test import TestCase

from apps.channels_core.models import Channel, ChannelAdAccount
from apps.channels_core.tests.ad_account_scope_mixins import (
    AccountScopeMixin,
    RepositoryScopeMixin,
    TotalSpendDedupeMixin,
)
from apps.google_ads.adapters import GoogleAdsSpendAdapter
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsAccountDailySpend,
    GoogleAdsConfig,
)
from apps.orders.repositories import OrderRepository

_CUSTOMER_ID = "3000000000"


def _config(i: int) -> GoogleAdsConfig:
    return GoogleAdsConfig.objects.create(
        name=f"MCC {i}", developer_token="d", client_id="c", client_secret="s",
        refresh_token="r", login_customer_id=f"{i}000000000",
    )


def _account(config: GoogleAdsConfig, name="Delax.vn", customer_id=_CUSTOMER_ID) -> GoogleAdsAccount:
    return GoogleAdsAccount.objects.create(config=config, name=name, customer_id=customer_id)


class GoogleAccountScopeTests(AccountScopeMixin, TestCase):
    def setUp(self):
        self.config_a = _config(1)
        self.config_b = _config(2)
        self.shell = _account(self.config_a)
        self.real = _account(self.config_b)
        self.other = _account(self.config_b, name="Khác", customer_id="4000000000")
        self.adapter = GoogleAdsSpendAdapter()


class GoogleTotalSpendDedupeTests(TotalSpendDedupeMixin, TestCase):
    def setUp(self):
        self.configs = [_config(i) for i in range(1, 3)]
        self.copies = [_account(config) for config in self.configs]
        self.other = _account(self.configs[1], name="Khác", customer_id="4000000000")
        self.adapter = GoogleAdsSpendAdapter()

    def _spend(self, account, day, amount):
        return GoogleAdsAccountDailySpend.objects.create(
            ad_account=account, date=day, spend=Decimal(amount),
        )


class OrderRepositoryGoogleAdsScopeTests(RepositoryScopeMixin, TestCase):
    username_prefix = "gads"
    scope_fn = staticmethod(OrderRepository.google_ads_account_scope)
    ids_fn = staticmethod(OrderRepository.accessible_google_ads_account_ids)

    def setUp(self):
        self.shell = _account(_config(1))
        self.real = _account(_config(2))
        self.channel = Channel.objects.create(code="g-delax", name="Google Delax")
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.GOOGLE,
            account_id=self.shell.id,
        )
