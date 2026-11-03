"""Phạm vi TKQC phải tính theo id ngoài của Facebook, không theo PK.

Cùng một tài khoản quảng cáo THẬT tồn tại nhiều dòng `FacebookAdAccount`: unique là
`(app, account_id)` nên mỗi FacebookApp có quyền vào cùng Business Manager tạo một
dòng riêng. Mapping kênh chỉ trỏ được MỘT dòng, trong khi ad + chi tiêu nằm dưới dòng
của app khác — prod 2026-08-10: kênh trỏ PK của app 1, toàn bộ chi tiêu nằm dưới app
4, nhân viên ads thấy bảng trắng mà không có tín hiệu nào.

Thân test dùng chung với bản Google qua mixin ở
`apps/channels_core/tests/ad_account_scope_mixins.py`; file này chỉ dựng dữ liệu
Facebook + các ca riêng của Facebook.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.channels_core.models import Channel, ChannelAdAccount
from apps.channels_core.tests.ad_account_scope_mixins import (
    AccountScopeMixin,
    RepositoryScopeMixin,
    TotalSpendDedupeMixin,
)
from apps.facebook_ads.adapters import FacebookAdsSpendAdapter
from apps.facebook_ads.models import (
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookApp,
)
from apps.orders.repositories import OrderRepository

_ACCOUNT_ID = "25322428374101900"


def _app(i: int) -> FacebookApp:
    return FacebookApp.objects.create(name=f"App {i}", app_id=str(i), app_secret="s", access_token="t")


def _account(app: FacebookApp, name="LỢI_TK7_HKD", account_id=_ACCOUNT_ID) -> FacebookAdAccount:
    return FacebookAdAccount.objects.create(
        app=app, name=name, account_id=account_id, fb_id=f"act_{account_id}",
    )


class FacebookAccountScopeTests(AccountScopeMixin, TestCase):
    def setUp(self):
        self.app1 = _app(1)
        self.app4 = _app(4)
        # Cùng một TKQC thật, hai dòng vì unique là (app, account_id).
        self.shell = _account(self.app1)
        self.real = _account(self.app4)
        self.other = _account(self.app4, name="TKQC khác", account_id="999")
        self.adapter = FacebookAdsSpendAdapter()

    def test_two_real_accounts_count_as_two(self):
        scope = self.adapter.account_scope([self.shell.id, self.other.id])
        self.assertEqual(scope.count, 2)
        self.assertEqual(
            sorted(scope.ids), sorted([self.shell.id, self.real.id, self.other.id]),
        )


class FacebookTotalSpendDedupeTests(TotalSpendDedupeMixin, TestCase):
    """``total_spend`` đếm mỗi TKQC THẬT một lần cho mỗi ngày.

    Đo prod 2026-08-19: `facebook_ad_accounts` có 240 dòng / 60 TKQC thật và cả
    240 đều ``status='active'``, nên ``sync_daily_spend_for_app`` — vốn chạy vòng
    qua MỌI ``FacebookApp`` — ghi daily spend đầy đủ cho cả 4 bản sao. Unique
    ``(ad_account, date)`` không chặn được vì ``ad_account_id`` khác nhau: kỳ
    01-19/08 mỗi app đúng 30 TKQC và đúng 632.085.746đ. ``SUM`` cả bảng vì vậy
    thổi ô "Chi phí FB + Google Ads" của Báo cáo tổng lên đúng 4.000 lần —
    2,89 tỉ hiển thị thay cho 807 triệu thật.

    Báo cáo Ads không dính vì nó lọc theo ``ChannelAdAccount`` nên chỉ chạm một
    bản sao; hai màn hình lệch nhau 2 tỉ trên cùng một kỳ.
    """

    def setUp(self):
        self.apps = [_app(i) for i in range(1, 5)]
        # Một TKQC THẬT, bốn dòng DB — unique là (app, account_id).
        self.copies = [_account(app) for app in self.apps]
        self.other = _account(self.apps[3], name="TKQC khác", account_id="999")
        self.adapter = FacebookAdsSpendAdapter()

    def _spend(self, account, day, amount):
        return FacebookAdAccountDailySpend.objects.create(
            ad_account=account, date=day, spend=Decimal(amount),
        )

    def test_each_day_is_deduped_on_its_own(self):
        for copy in self.copies:
            self._spend(copy, date(2026, 8, 1), "100000")
            self._spend(copy, date(2026, 8, 2), "50000")
        self.assertEqual(self.adapter.total_spend(), Decimal("150000"))


class OrderRepositoryAdsScopeTests(RepositoryScopeMixin, TestCase):
    """Luật phân quyền phải trả PK đã trải, không phải PK thô của mapping kênh."""

    username_prefix = "ads"
    scope_fn = staticmethod(OrderRepository.ads_ad_account_scope)
    ids_fn = staticmethod(OrderRepository.accessible_ads_ad_account_ids)

    def setUp(self):
        self.shell = _account(_app(1))
        self.real = _account(_app(4))
        self.channel = Channel.objects.create(code="fb-loi", name="Facebook Lợi")
        # Kênh trỏ đúng bản "vỏ rỗng" của app 1 — tái hiện dữ liệu prod.
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.shell.id,
        )
