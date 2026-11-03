"""Gán chiến dịch Facebook ↔ sản phẩm: quyền, danh sách kèm chi phí, gán/gỡ.

Luật nghiệp vụ được chốt ở docs/ads-campaign-product-mapping.md: 1 chiến dịch chỉ
chạy 1 SP; parser chỉ gợi ý và KHÔNG BAO GIỜ ghi đè bản người gán tay.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.catalog.sku_text_match import assign_products_to_campaigns
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdCampaign,
    FacebookAdDailySpend,
    FacebookApp,
)
from apps.users.tests.helpers import make_user_with_permissions


class CampaignProductsApiTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="HT-TK4", account_id="9", fb_id="act_9",
        )
        self.product = Product.objects.create(name="Bồn ngâm chân DC30", sku="DC30")
        self.campaign = FacebookAdCampaign.objects.create(
            fb_campaign_id="c1",
            name="Desiglobal_BonNgamChan_XYZ_LDP_0DC30_030826_Page 2",
            ad_account=self.account,
        )
        ad = FacebookAd.objects.create(
            fb_ad_id="a1", ad_account=self.account, campaign=self.campaign,
        )
        FacebookAdDailySpend.objects.create(
            ad=ad, date=date(2026, 8, 1), spend=Decimal("100.00"), impressions=10, clicks=1,
        )
        self.url = reverse("fb-campaign-products")

    def _client(self, *codes):
        client = APIClient()
        client.force_authenticate(make_user_with_permissions("u", codes))
        return client

    def _params(self):
        return {"since": "2026-08-01", "until": "2026-08-31"}

    def test_view_permission_can_read(self):
        response = self._client("ads-report:view").get(self.url, self._params())

        self.assertEqual(response.status_code, 200)
        row = response.data["rows"][0]
        self.assertEqual(row["campaign_id"], "c1")
        # Chi phí đã nhân hệ số hiển thị của Facebook — 1.08 vì kỳ xem bắt đầu
        # 01/08/2026 (mốc đổi hệ số), xem `facebook_ads_cost_multiplier`.
        self.assertEqual(row["cost"], "108.00")
        self.assertIsNone(row["product_id"])
        self.assertEqual(response.data["unassigned_cost"], "108.00")

    def test_view_permission_alone_cannot_assign(self):
        """Ai xem được Báo cáo ads cũng sửa được thì chi phí theo SP thành vô chủ."""
        response = self._client("ads-report:view").patch(
            self.url, {"id": self.campaign.id, "product_id": self.product.id}, format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_edit_permission_assigns_and_marks_manual(self):
        response = self._client("ads-report:view", "ads-report:edit").patch(
            self.url, {"id": self.campaign.id, "product_id": self.product.id}, format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.product_id, self.product.id)
        self.assertEqual(self.campaign.product_source, FacebookAdCampaign.SOURCE_MANUAL)
        self.assertIsNotNone(self.campaign.product_updated_by_id)
        self.assertIsNotNone(self.campaign.product_updated_at)

    def test_unassign_stays_manual_so_parser_does_not_reassign(self):
        client = self._client("ads-report:view", "ads-report:edit")
        client.patch(
            self.url, {"id": self.campaign.id, "product_id": self.product.id}, format="json",
        )
        client.patch(self.url, {"id": self.campaign.id, "product_id": None}, format="json")

        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.product_id)
        self.assertEqual(self.campaign.product_source, FacebookAdCampaign.SOURCE_MANUAL)

        # Parser chạy lại: tên chiến dịch VẪN chứa 0DC30 nhưng phải bỏ qua dòng này.
        assign_products_to_campaigns(FacebookAdCampaign.objects.all())

        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.product_id)

    def test_unknown_product_is_rejected(self):
        response = self._client("ads-report:view", "ads-report:edit").patch(
            self.url, {"id": self.campaign.id, "product_id": 999999}, format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_filter_unassigned_only(self):
        FacebookAdCampaign.objects.create(
            fb_campaign_id="c2", name="Đã gán", ad_account=self.account,
            product=self.product, product_source=FacebookAdCampaign.SOURCE_MANUAL,
        )
        response = self._client("ads-report:view").get(
            self.url, {**self._params(), "assigned": "no"},
        )

        self.assertEqual([r["campaign_id"] for r in response.data["rows"]], ["c1"])


class CampaignProductsAccountFilterTests(TestCase):
    """Bộ lọc TKQC của màn Gán sản phẩm.

    TKQC gom theo ``account_id`` (id bên Facebook) chứ không theo pk: một TKQC thật
    nằm ở nhiều dòng — mỗi FacebookApp một dòng (lớp lỗi MR !1199).
    """

    def setUp(self):
        self.app_a = FacebookApp.objects.create(
            name="App A", app_id="1", app_secret="s", access_token="t",
        )
        self.app_b = FacebookApp.objects.create(
            name="App B", app_id="2", app_secret="s", access_token="t",
        )
        # Cùng một TKQC thật, hai dòng vì được đồng bộ qua hai app.
        self.acc_a1 = FacebookAdAccount.objects.create(
            app=self.app_a, name="HT-TK4", account_id="9", fb_id="act_9",
        )
        self.acc_a2 = FacebookAdAccount.objects.create(
            app=self.app_b, name="HT-TK4", account_id="9", fb_id="act_9",
        )
        self.acc_b = FacebookAdAccount.objects.create(
            app=self.app_a, name="HT-TK7", account_id="7", fb_id="act_7",
        )
        self._campaign("c1", self.acc_a1, Decimal("100.00"))
        self._campaign("c2", self.acc_a2, Decimal("200.00"))
        self._campaign("c3", self.acc_b, Decimal("50.00"))
        self.url = reverse("fb-campaign-products")

    def _campaign(self, code, account, spend):
        campaign = FacebookAdCampaign.objects.create(
            fb_campaign_id=code, name=f"CD {code}", ad_account=account,
        )
        ad = FacebookAd.objects.create(
            fb_ad_id=f"ad-{code}", ad_account=account, campaign=campaign,
        )
        FacebookAdDailySpend.objects.create(ad=ad, date=date(2026, 8, 1), spend=spend)
        return campaign

    def _get(self, **extra):
        client = APIClient()
        client.force_authenticate(make_user_with_permissions("u", ["ads-report:view"]))
        return client.get(self.url, {"since": "2026-08-01", "until": "2026-08-31", **extra})

    def test_account_list_groups_duplicate_rows_of_the_same_account(self):
        response = self._get()

        self.assertEqual(
            response.data["accounts"],
            [
                {"platform": "facebook", "id": "9", "name": "HT-TK4"},
                {"platform": "facebook", "id": "7", "name": "HT-TK7"},
            ],
        )

    def test_filter_covers_every_row_of_the_same_account(self):
        """Lọc theo pk sẽ chỉ ra c1; đúng phải ra cả c2 vì cùng một TKQC thật."""
        response = self._get(account="facebook:9")

        self.assertEqual(
            sorted(r["campaign_id"] for r in response.data["rows"]), ["c1", "c2"],
        )
        # Chi phí cũng phải thu hẹp theo, không lấy nhầm phần của TKQC khác.
        self.assertEqual(response.data["unassigned_cost"], "324.00")

    def test_account_list_does_not_shrink_with_the_current_filters(self):
        """Ô lọc mà tự rụng lựa chọn theo chính lựa chọn vừa bấm thì không quay lại được."""
        response = self._get(account="facebook:7", assigned="yes", q="không-khớp-gì")

        self.assertEqual(response.data["rows"], [])
        self.assertEqual([a["id"] for a in response.data["accounts"]], ["9", "7"])

    def test_picking_a_google_account_empties_this_platform(self):
        response = self._get(account="google:1234567890")

        self.assertEqual(response.data["rows"], [])
        self.assertEqual(response.data["unassigned_cost"], "0.00")
        self.assertEqual(len(response.data["accounts"]), 2)


class AssignProductsToCampaignsTests(TestCase):
    """Parser áp lên bảng chiến dịch — phần dùng chung với Google."""

    def setUp(self):
        self.product = Product.objects.create(name="Máy massage bàn chân", sku="MSC06")

    def test_assigns_and_marks_auto(self):
        campaign = FacebookAdCampaign.objects.create(
            fb_campaign_id="c1", name="DMG - MSC06 - Audience",
        )

        result = assign_products_to_campaigns(FacebookAdCampaign.objects.all())

        campaign.refresh_from_db()
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["assigned"], 1)
        self.assertEqual(result["guessed"], 0)
        self.assertEqual(campaign.product_id, self.product.id)
        self.assertEqual(campaign.product_source, FacebookAdCampaign.SOURCE_AUTO)

    def test_never_overwrites_a_manual_assignment(self):
        other = Product.objects.create(name="Khác", sku="BNC02")
        campaign = FacebookAdCampaign.objects.create(
            fb_campaign_id="c1",
            name="DMG - MSC06 - Audience",
            product=other,
            product_source=FacebookAdCampaign.SOURCE_MANUAL,
        )

        assign_products_to_campaigns(FacebookAdCampaign.objects.all())

        campaign.refresh_from_db()
        self.assertEqual(campaign.product_id, other.id)

    def test_leaves_unrecognized_names_alone(self):
        campaign = FacebookAdCampaign.objects.create(fb_campaign_id="c1", name="06/05 bài 3")

        result = assign_products_to_campaigns(FacebookAdCampaign.objects.all())

        campaign.refresh_from_db()
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["assigned"], 0)
        self.assertIsNone(campaign.product_id)


class BackfillCommandTests(TestCase):
    """Lệnh backfill chạy tay — đường thoát khi không muốn chờ lịch beat mỗi giờ."""

    def setUp(self):
        self.promax = Product.objects.create(name="Thảm massage TMS01", sku="TMS01-PROMAX")
        Product.objects.create(name="TMS01", sku="TMS01")  # SKU rác cùng mã gốc
        self.campaign = FacebookAdCampaign.objects.create(
            fb_campaign_id="c1", name="Desiglobal_ThamMassagePromax_XYZ_LDP_TMS01_220826",
        )

    def _run(self, *args, command="backfill_fb_campaign_products"):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(command, *args, stdout=out)
        return out.getvalue()

    def test_unified_command_with_provider_flag(self):
        """Lệnh gộp ở orders resolve provider Facebook qua registry; tên cũ chỉ là alias."""
        output = self._run(
            "--provider", "facebook", "--apply", command="backfill_ads_campaign_products",
        )

        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.product_id)
        self.assertIn("Facebook", output)

    def test_unified_command_requires_provider(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run(command="backfill_ads_campaign_products")

    def test_dry_run_writes_nothing(self):
        output = self._run()

        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.product_id)
        self.assertIn("XEM THỬ", output)

    def test_apply_assigns_the_variant_that_actually_sells(self):
        """Không có dữ liệu đơn thì hai biến thể hoà nhau — vẫn không được ra SKU rác.

        Bảng xếp hạng rỗng nên `pick` rơi về id nhỏ nhất; điều được chốt ở đây là lệnh
        chạy được và đánh dấu ĐOÁN, còn luật chọn biến thể theo doanh thu đã test kỹ ở
        apps/catalog.
        """
        self._run("--apply")

        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.product_id)
        self.assertEqual(self.campaign.product_source, FacebookAdCampaign.SOURCE_AUTO_LOOSE)

    def test_never_touches_a_manual_row(self):
        other = Product.objects.create(name="Khác", sku="BNC02")
        self.campaign.product = other
        self.campaign.product_source = FacebookAdCampaign.SOURCE_MANUAL
        self.campaign.save()

        self._run("--apply", "--loose")

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.product_id, other.id)
