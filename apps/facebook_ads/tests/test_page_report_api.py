"""Báo cáo chi phí theo page — giai đoạn A: chưa có doanh thu."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdDailySpend,
    FacebookApp,
    FacebookPage,
)
from apps.orders._repositories_reports import ADS_DAILY_COST_MULTIPLIER
from apps.users.models import DataScope
from apps.users.tests.helpers import make_user_with_sections


class PageReportApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Helper chuẩn của repo: gán đủ permission của section + DataScope.ALL, nên
        # `_accessible_ads_ad_account_ids` trả toàn bộ TKQC (xem test_sync_view_token_state.py).
        cls.user = make_user_with_sections("ads_ops", ["ads-report"])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )
        self.page = FacebookPage.objects.create(fb_page_id="111", name="Page A")
        self.ad = FacebookAd.objects.create(
            fb_ad_id="A1", ad_account=self.account, page=self.page,
        )
        self.orphan = FacebookAd.objects.create(
            fb_ad_id="A2", ad_account=self.account, page=None,
        )
        FacebookAdDailySpend.objects.create(
            ad=self.ad, date=date(2026, 7, 1), spend=Decimal("100.00"),
            impressions=1000, clicks=50,
        )
        FacebookAdDailySpend.objects.create(
            ad=self.orphan, date=date(2026, 7, 1), spend=Decimal("40.00"),
        )
        # Page có phát sinh dòng chi tiêu nhưng tổng đúng bằng 0 — bắt lỗi
        # `Decimal("0.00") or Decimal("0")` nuốt mất 2 số thập phân.
        self.zero_page = FacebookPage.objects.create(fb_page_id="222", name="Page Zero")
        self.zero_ad = FacebookAd.objects.create(
            fb_ad_id="A3", ad_account=self.account, page=self.zero_page,
        )
        FacebookAdDailySpend.objects.create(
            ad=self.zero_ad, date=date(2026, 7, 1), spend=Decimal("0.00"),
        )

    def _get(self, **params):
        params.setdefault("since", "2026-07-01")
        params.setdefault("until", "2026-07-01")
        return self.client.get("/api/facebook-ads/reports/page", params)

    def test_groups_spend_by_page(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        rows = {r["page_id"]: r for r in response.json()["rows"]}
        self.assertEqual(rows["111"]["cost"], "110.00")  # 100.00 × 1.1
        self.assertEqual(rows["111"]["page_name"], "Page A")

    def test_unresolved_ads_get_their_own_row(self):
        """Chi phí của ad chưa rõ page phải lộ ra, không được biến mất khỏi tổng."""
        rows = {r["page_id"]: r for r in self._get().json()["rows"]}
        self.assertIn("", rows)
        self.assertEqual(rows[""]["page_name"], "Không rõ page")
        self.assertEqual(rows[""]["cost"], "44.00")  # 40.00 × 1.1

    def test_revenue_is_null_in_phase_a(self):
        """Chưa go-live doanh thu: null, KHÔNG phải 0."""
        body = self._get().json()
        self.assertIsNone(body["revenue_available_from"])
        for row in body["rows"]:
            self.assertIsNone(row["revenue"])
            self.assertIsNone(row["ratio_pct"])

    def test_falls_back_to_id_when_page_name_empty(self):
        FacebookPage.objects.filter(fb_page_id="111").update(name="")
        rows = {r["page_id"]: r for r in self._get().json()["rows"]}
        self.assertEqual(rows["111"]["page_name"], "Page 111")

    def test_rejects_invalid_dates(self):
        self.assertEqual(self._get(since="not-a-date").status_code, 400)

    def test_rejects_reversed_range(self):
        response = self._get(since="2026-07-05", until="2026-07-01")
        self.assertEqual(response.status_code, 400)

    def test_requires_authentication(self):
        client = APIClient()
        response = client.get(
            "/api/facebook-ads/reports/page",
            {"since": "2026-07-01", "until": "2026-07-01"},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_see_all_user_gets_spend_from_unmapped_accounts(self):
        """TKQC chưa map vào kênh nào vẫn phải hiện chi phí cho user xem-toàn-bộ.

        Fixture này KHÔNG tạo Channel/ChannelAdAccount nào — nếu scope trả list rỗng
        thay vì None thì toàn bộ chi phí biến mất và test này bắt được.
        """
        rows = self._get().json()["rows"]
        self.assertEqual(
            sum(float(r["cost"]) for r in rows), 154.0,  # (100 + 40) × 1.1
        )

    def test_period_from_august_2026_uses_the_new_multiplier(self):
        """Kỳ bắt đầu trong tháng 8/2026 → ×1.08; các kỳ cũ trong file này vẫn ×1.1.
        Bảng mốc đầy đủ: `apps/orders/tests/test_facebook_ads_cost_multiplier.py`."""
        FacebookAdDailySpend.objects.create(
            ad=self.ad, date=date(2026, 8, 1), spend=Decimal("100.00"),
        )

        rows = {
            r["page_id"]: r
            for r in self._get(since="2026-08-01", until="2026-08-31").json()["rows"]
        }

        self.assertEqual(rows["111"]["cost"], "108.00")

    def test_zero_cost_page_renders_two_decimals(self):
        """Chi phí đúng bằng 0 vẫn phải là "0.00", không rớt số thập phân."""
        rows = {r["page_id"]: r for r in self._get().json()["rows"]}
        self.assertEqual(rows["222"]["cost"], "0.00")

    def test_applies_same_cost_multiplier_as_channel_report(self):
        """Hai tab của cùng một màn hình phải cùng hệ số.

        Tab "Theo kênh" nhân spend FB với `ADS_DAILY_COST_MULTIPLIER` (1.1 cho kỳ
        TRƯỚC mốc 01/08/2026 — kỳ của fixture này là tháng 7). Nếu tab "Theo page"
        quên nhân, cùng một khoảng ngày cho ra hai con số lệch nhau mà không có gì
        giải thích — người đọc sẽ tin nhầm một trong hai. Test này chốt hệ số ĐANG
        được áp dụng, và chốt luôn rằng nó lấy từ đúng một nguồn: đổi hằng số ở báo
        cáo kênh mà quên chỗ này thì test đỏ ngay.
        """
        raw = Decimal("100.00")
        expected = f"{raw * ADS_DAILY_COST_MULTIPLIER:.2f}"
        rows = {r["page_id"]: r for r in self._get().json()["rows"]}
        self.assertEqual(rows["111"]["cost"], expected)
        self.assertNotEqual(rows["111"]["cost"], f"{raw:.2f}")


class PageReportScopeTests(TestCase):
    """User scope CHANNEL chỉ thấy chi phí của TKQC gắn với kênh mình phụ trách.

    Toàn bộ 8 test ở `PageReportApiTests` dùng user DataScope.ALL nên nhánh lọc
    `if ad_account_ids is not None` trong `aggregate_page_cost` chưa từng chạy —
    một regression âm thầm ở đó (sai lookup, sai join) sẽ để lộ chi phí TKQC của
    người khác mà không test nào bắt được. Đây là test bắt lỗ hổng phân quyền đó,
    theo đúng fixture pattern của `test_ads_report_channel_scope.py`.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user_with_sections(
            "ads_scoped", ["ads-report"], scope=DataScope.CHANNEL,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.app = FacebookApp.objects.create(
            name="App", app_id="scope-app", app_secret="s", access_token="t",
        )

        # TKQC gắn với kênh user phụ trách -> phải thấy chi phí.
        self.reachable_account = FacebookAdAccount.objects.create(
            app=self.app, name="Reachable", account_id="1", fb_id="act_1",
        )
        self.ch_reachable = Channel.objects.create(code="fb-reachable", name="FB Reachable")
        UserChannel.objects.create(user=self.user, channel=self.ch_reachable, is_active=True)
        ChannelAdAccount.objects.create(
            channel=self.ch_reachable,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.reachable_account.id,
        )
        self.reachable_page = FacebookPage.objects.create(fb_page_id="r1", name="Reachable Page")
        self.reachable_ad = FacebookAd.objects.create(
            fb_ad_id="R1", ad_account=self.reachable_account, page=self.reachable_page,
        )
        FacebookAdDailySpend.objects.create(
            ad=self.reachable_ad, date=date(2026, 7, 1), spend=Decimal("120.00"),
        )

        # TKQC gắn với kênh KHÁC, user không phụ trách -> không được thấy.
        self.other_account = FacebookAdAccount.objects.create(
            app=self.app, name="Other", account_id="2", fb_id="act_2",
        )
        self.ch_other = Channel.objects.create(code="fb-other", name="FB Other")
        ChannelAdAccount.objects.create(
            channel=self.ch_other,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.other_account.id,
        )
        self.other_page = FacebookPage.objects.create(fb_page_id="o1", name="Other Page")
        self.other_ad = FacebookAd.objects.create(
            fb_ad_id="O1", ad_account=self.other_account, page=self.other_page,
        )
        FacebookAdDailySpend.objects.create(
            ad=self.other_ad, date=date(2026, 7, 1), spend=Decimal("999.00"),
        )

    def test_scoped_user_sees_only_own_channel_ad_account_spend(self):
        response = self.client.get(
            "/api/facebook-ads/reports/page",
            {"since": "2026-07-01", "until": "2026-07-01"},
        )
        self.assertEqual(response.status_code, 200)
        rows = {r["page_id"]: r for r in response.json()["rows"]}
        self.assertEqual(rows["r1"]["cost"], "132.00")  # 120.00 × 1.1
        self.assertNotIn("o1", rows)

    def test_sees_spend_when_channel_maps_a_duplicate_row_of_the_same_account(self):
        """Cùng lỗ hổng với báo cáo chiến dịch: hai view dùng chung luật phạm vi."""
        other_app = FacebookApp.objects.create(
            name="App khác", app_id="dup-app", app_secret="s", access_token="t",
        )
        # Bản sao của `reachable_account` dưới app khác — nơi ad thật nằm.
        duplicate = FacebookAdAccount.objects.create(
            app=other_app,
            name=self.reachable_account.name,
            account_id=self.reachable_account.account_id,
            fb_id=self.reachable_account.fb_id,
        )
        page = FacebookPage.objects.create(fb_page_id="d1", name="Dup Page")
        ad = FacebookAd.objects.create(fb_ad_id="D1", ad_account=duplicate, page=page)
        FacebookAdDailySpend.objects.create(
            ad=ad, date=date(2026, 7, 1), spend=Decimal("200.00"),
        )

        response = self.client.get(
            "/api/facebook-ads/reports/page",
            {"since": "2026-07-01", "until": "2026-07-01"},
        )
        rows = {r["page_id"]: r for r in response.json()["rows"]}
        self.assertEqual(rows["d1"]["cost"], "220.00")  # 200.00 × 1.1
