"""Mixin test dùng chung cho phạm vi TKQC của hai adapter quảng cáo (facebook_ads, google_ads).

Cùng một tài khoản quảng cáo THẬT có thể tồn tại nhiều dòng DB (Facebook: unique
`(app, account_id)`; Google: unique `(config, customer_id)`), nên mapping kênh chỉ
trỏ được MỘT dòng trong khi chi tiêu nằm dưới dòng khác — prod 2026-08-10 (FB) và
lớp lỗi y hệt chờ sẵn ở Google. Hai app nằm CÙNG TẦNG độc lập trong .importlinter
nên không import test của nhau; mixin đặt ở channels_core (tầng dưới cả hai) và
KHÔNG import model/adapter/repository của app nào — subclass cung cấp:

- ``setUp`` dựng ``self.adapter``, ``self.shell`` / ``self.real`` (hai dòng của cùng
  một TKQC thật), ``self.other`` (TKQC khác) — cho ``AccountScopeMixin``;
- ``self.copies`` (mọi bản sao của một TKQC thật), ``self.other``, ``self.adapter``,
  ``_spend(account, day, amount)`` — cho ``TotalSpendDedupeMixin``;
- ``self.channel`` đã map tới ``self.shell``, ``self.real``, ``scope_fn`` /
  ``ids_fn`` (hàm của OrderRepository, truyền qua ``staticmethod``) và
  ``username_prefix`` — cho ``RepositoryScopeMixin``.

Không phải test: file không khớp ``test_*.py`` nên pytest không collect; các class
chỉ được chạy qua subclass ``(Mixin, TestCase)`` ở từng app.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.channels_core.models import UserChannel
from apps.users.models import DataScope
from apps.users.tests.helpers import make_user_with_sections


class AccountScopeMixin:
    """``adapter.account_scope(ids)`` trải PK ra mọi bản sao và đếm TKQC THẬT."""

    def test_expands_to_every_duplicate_of_the_same_account(self):
        scope = self.adapter.account_scope([self.shell.id])
        self.assertEqual(sorted(scope.ids), sorted([self.shell.id, self.real.id]))

    def test_counts_real_accounts_not_duplicate_rows(self):
        # 2 dòng DB nhưng chỉ 1 TKQC thật — đếm len(ids) sẽ nói dối.
        self.assertEqual(self.adapter.account_scope([self.shell.id]).count, 1)

    def test_does_not_leak_unrelated_accounts(self):
        self.assertNotIn(self.other.id, self.adapter.account_scope([self.shell.id]).ids)

    def test_empty_input_gives_empty_scope(self):
        scope = self.adapter.account_scope([])
        self.assertEqual(scope.ids, [])
        self.assertEqual(scope.count, 0)


class TotalSpendDedupeMixin:
    """``adapter.total_spend`` đếm mỗi TKQC THẬT một lần cho mỗi ngày (lấy bản lớn nhất)."""

    DAY = date(2026, 8, 1)

    def test_copies_of_one_account_count_once(self):
        for copy in self.copies:
            self._spend(copy, self.DAY, "100000")
        self.assertEqual(self.adapter.total_spend(), Decimal("100000"))

    def test_distinct_real_accounts_still_add_up(self):
        for copy in self.copies:
            self._spend(copy, self.DAY, "100000")
        self._spend(self.other, self.DAY, "30000")
        self.assertEqual(self.adapter.total_spend(), Decimal("130000"))

    def test_takes_the_largest_copy_when_sync_drifts(self):
        """Sync chạy lệch nhịp giữa các app/MCC: chi tiêu trong ngày chỉ tăng nên bản
        lớn nhất là bản mới nhất — lấy ``max`` thay vì bản chốt sớm còn thiếu."""
        amounts = ["99000"] + ["100000"] * (len(self.copies) - 1)
        for copy, amount in zip(self.copies, amounts, strict=True):
            self._spend(copy, self.DAY, amount)
        self.assertEqual(self.adapter.total_spend(), Decimal("100000"))

    def test_date_range_still_applies_after_dedupe(self):
        for copy in self.copies:
            self._spend(copy, date(2026, 7, 31), "70000")
            self._spend(copy, self.DAY, "100000")
        total = self.adapter.total_spend(
            date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
        )
        self.assertEqual(total, Decimal("100000"))

    def test_no_rows_gives_zero(self):
        self.assertEqual(self.adapter.total_spend(), Decimal("0"))


class RepositoryScopeMixin:
    """Luật phân quyền trả PK đã trải, không phải PK thô của mapping kênh.

    ``ids=None``/``count=None`` = KHÔNG bị lọc (DataScope.ALL) — khác hẳn ``[]``/``0``
    (scope CHANNEL nhưng chưa gán kênh): FE chỉ hiện băng "chưa gán TKQC nào" khi
    cả hai nguồn trả 0, trả ``None`` nhầm là băng cảnh báo tắt vĩnh viễn.
    """

    username_prefix = "ads"

    def _scoped_user(self, username=None):
        user = make_user_with_sections(
            username or f"{self.username_prefix}_scoped", ["ads-report"], scope=DataScope.CHANNEL
        )
        UserChannel.objects.create(user=user, channel=self.channel, is_active=True)
        return user

    def test_all_scope_user_is_not_filtered(self):
        user = make_user_with_sections(f"{self.username_prefix}_all", ["ads-report"], scope=DataScope.ALL)
        scope = self.scope_fn(user)
        self.assertIsNone(scope.ids)
        self.assertIsNone(scope.count)
        self.assertIsNone(self.ids_fn(user))

    def test_scoped_user_gets_expanded_ids(self):
        ids = self.ids_fn(self._scoped_user())
        self.assertEqual(sorted(ids), sorted([self.shell.id, self.real.id]))

    def test_scope_counts_real_accounts(self):
        self.assertEqual(self.scope_fn(self._scoped_user()).count, 1)

    def test_user_without_channel_gets_empty_scope(self):
        user = make_user_with_sections(
            f"{self.username_prefix}_none", ["ads-report"], scope=DataScope.CHANNEL
        )
        scope = self.scope_fn(user)
        self.assertEqual(scope.ids, [])
        self.assertEqual(scope.count, 0)
