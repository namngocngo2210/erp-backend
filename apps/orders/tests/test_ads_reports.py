"""Báo cáo ads (/api/orders/reports/ads) — gộp từ 5 file test theo domain
(tái cấu trúc 2026-08-05, xem docs/test-restructure-2026-08-05.md):
test_ads_report_demographics, test_ads_report_channel_scope,
test_ads_report_top_products_delivered, test_ads_report_scope,
test_ads_staff_multi_department. Docstring gốc của từng file giữ nguyên
làm docstring class tương ứng.
"""
import pytest
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.infrastructure.repositories.channel_repositories_impl import (
    ChannelRepository,
)
from apps.channels_core.models import Channel, ChannelAdAccount, UserChannel
from apps.customers.models import Customer
from apps.facebook_ads.models import (
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookApp,
)
from apps.orders._repositories_reports import sale_report_channel_ids
from apps.orders.models import Order, OrderAssessment, OrderItem
from apps.catalog.models import Product
from apps.orders.repositories import OrderRepository
from apps.users.constants import DepartmentID
from apps.users.department_membership import user_in_department
from apps.users.models import (
    DataScope,
    Department,
    Permission,
    Role,
    RolePermission,
    RoleSectionScope,
    User,
    UserDepartment,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_admin


# Cache report/endpoint: mỗi test một LocMemCache riêng (backend/conftest.py), không cache.clear() Redis dùng chung.
pytestmark = pytest.mark.usefixtures("isolated_cache")


class AdsReportDemographicsTests(TestCase):
    """Báo cáo ads — 2 khối cuối trang: phân bổ theo tỉnh/thành + giới tính.

    Cùng base queryset với revenue_by_channel (đơn có nguồn, kênh có NV Ads gán,
    đơn đã chốt): scope kênh / loại đơn hủy phải đồng nhất với các khối trên.
    Province gộp theo giá trị trim, rỗng → null; gender lấy từ Customer.gender,
    null = chưa có thông tin (nhãn "Không rõ" do FE hiển thị).
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-ads-demographics")
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
        self.ads = User.objects.create_user(username="ads-demographics-nv", password="x")
        User.objects.filter(pk=self.ads.pk).update(department_id=DepartmentID.ADS)

        # id tường minh (xa SALE_FB_CHANNEL_IDS=24): insert id cụ thể không đẩy
        # sequence của Postgres — tránh làm test khác chạy sau rơi trúng id 24
        # (sequence không rollback theo transaction của TestCase).
        self.ch_ads = Channel.objects.create(
            id=910001, code="fb-demo-staffed", name="FB Staffed"
        )
        UserChannel.objects.create(user=self.ads, channel=self.ch_ads, is_active=True)
        # Kênh KHÔNG gán NV Ads -> đơn của kênh này không được tính.
        self.ch_noads = Channel.objects.create(
            id=910002, code="fb-demo-unstaffed", name="FB Unstaffed"
        )

        self.male = self._customer("0900000011", gender=1)
        self.female = self._customer("0900000012", gender=0)
        self.no_gender = self._customer("0900000013", gender=None)

    def _customer(self, phone, *, gender):
        return Customer.objects.create(
            full_name=f"KH {phone}", phone_raw=phone, phone_normalized=phone, gender=gender
        )

    def _order(
        self,
        total,
        *,
        customer,
        province="Hà Nội",
        channel=None,
        status=0,
        sales_status="closed",
    ):
        order = Order.objects.create(
            channel=channel or self.ch_ads,
            customer=customer,
            staff=self.ads,
            name="X",
            phone=customer.phone_raw,
            status=status,
            order_source="landing",
            province=province,
            total_after_discount=Decimal(total),
        )
        OrderAssessment.objects.create(order=order, sales_status_code=sales_status)
        return order

    def _payload(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/reports/ads")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_revenue_by_province_groups_and_sorts(self):
        self._order("100000", customer=self.male, province="Hà Nội", status=3)
        # "Hà Nội " (thừa khoảng trắng) phải gộp chung dòng "Hà Nội".
        self._order("50000", customer=self.female, province="Hà Nội ")
        self._order("200000", customer=self.female, province="Đà Nẵng")
        self._order("30000", customer=self.no_gender, province="")

        rows = self._payload()["revenue_by_province"]
        self.assertEqual(
            rows,
            [
                {
                    "province": "Đà Nẵng",
                    "closed_orders": 1,
                    "revenue": "200000.00",
                    "delivered_orders": 0,
                    "delivered_revenue": "0",
                },
                {
                    "province": "Hà Nội",
                    "closed_orders": 2,
                    "revenue": "150000.00",
                    "delivered_orders": 1,
                    "delivered_revenue": "100000.00",
                },
                {
                    "province": None,
                    "closed_orders": 1,
                    "revenue": "30000.00",
                    "delivered_orders": 0,
                    "delivered_revenue": "0",
                },
            ],
        )

    def test_revenue_by_gender_buckets(self):
        self._order("100000", customer=self.male, status=3)
        self._order("70000", customer=self.male)
        self._order("50000", customer=self.female)
        self._order("20000", customer=self.no_gender)

        rows = self._payload()["revenue_by_gender"]
        self.assertEqual(
            rows,
            [
                {
                    "gender": 1,
                    "closed_orders": 2,
                    "revenue": "170000.00",
                    "delivered_orders": 1,
                    "delivered_revenue": "100000.00",
                },
                {
                    "gender": 0,
                    "closed_orders": 1,
                    "revenue": "50000.00",
                    "delivered_orders": 0,
                    "delivered_revenue": "0",
                },
                {
                    "gender": None,
                    "closed_orders": 1,
                    "revenue": "20000.00",
                    "delivered_orders": 0,
                    "delivered_revenue": "0",
                },
            ],
        )

    def test_excludes_unstaffed_channel_and_non_closed(self):
        self._order("100000", customer=self.male)
        # Kênh chưa gán NV Ads -> ngoài phạm vi báo cáo.
        self._order("70000", customer=self.male, channel=self.ch_noads)
        # Chưa chốt -> không tính.
        self._order("50000", customer=self.female, sales_status="no_answer_1")
        # Đơn hủy -> không tính.
        self._order("30000", customer=self.female, status=6)

        payload = self._payload()
        self.assertEqual(
            [(r["province"], r["revenue"]) for r in payload["revenue_by_province"]],
            [("Hà Nội", "100000.00")],
        )
        self.assertEqual(
            [(r["gender"], r["revenue"]) for r in payload["revenue_by_gender"]],
            [(1, "100000.00")],
        )

    def test_channel_filter_applies(self):
        ch2 = Channel.objects.create(id=910003, code="fb-demo-2", name="FB 2")
        UserChannel.objects.create(user=self.ads, channel=ch2, is_active=True)
        self._order("100000", customer=self.male, province="Hà Nội")
        self._order("70000", customer=self.female, province="Đà Nẵng", channel=ch2)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(
            "/api/orders/reports/ads", {"channel_ids": str(ch2.id)}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [r["province"] for r in payload["revenue_by_province"]], ["Đà Nẵng"]
        )
        self.assertEqual([r["gender"] for r in payload["revenue_by_gender"]], [0])


class AdsReportChannelScopeTests(TestCase):
    """Báo cáo ads (/api/orders/reports/ads) chỉ tính kênh CÓ NV phòng Ads gán.

    Cùng nguồn sự thật với báo cáo sale (``sale_report_channel_ids``): kênh chưa gán
    NV Ads không vào báo cáo — cả phía doanh thu (revenue_by_channel / top_products /
    daily_breakdown) lẫn phía chi phí (spend của ad account map vào kênh chưa gán).
    Gán NV Ads vào kênh là kênh tự vào báo cáo, không cần sửa code.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-ads-report-scope")
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
        self.ads = User.objects.create_user(username="ads-report-nv", password="x")
        User.objects.filter(pk=self.ads.pk).update(department_id=DepartmentID.ADS)
        self.customer = Customer.objects.create(
            full_name="KH Ads", phone_raw="0900000001", phone_normalized="0900000001"
        )

        # Kênh CÓ gán NV ads -> trong phạm vi báo cáo ads.
        self.ch_ads = Channel.objects.create(code="fb-staffed", name="FB Staffed")
        UserChannel.objects.create(user=self.ads, channel=self.ch_ads, is_active=True)
        # Kênh KHÔNG gán ads -> ngoài phạm vi dù có đơn đã chốt.
        self.ch_noads = Channel.objects.create(code="fb-unstaffed", name="FB Unstaffed")

        self._order(self.ch_ads, Decimal("100000"))
        self._order(self.ch_noads, Decimal("70000"))

    def _order(self, channel, total):
        order = Order.objects.create(
            channel=channel,
            customer=self.customer,
            staff=self.ads,
            name="X",
            phone="0900000001",
            status=0,
            order_source="landing",
            total_after_discount=total,
        )
        OrderAssessment.objects.create(order=order, sales_status_code="closed")
        return order

    def _payload(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/reports/ads")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_revenue_by_channel_only_ads_staffed(self):
        codes = {row["channel__code"] for row in self._payload()["revenue_by_channel"]}
        self.assertEqual(codes, {"fb-staffed"})

    def test_daily_breakdown_only_ads_staffed(self):
        payload = self._payload()
        channel_codes = {
            channel["channel__code"]
            for day in payload["daily_breakdown"]
            for channel in day["channels"]
        }
        self.assertEqual(channel_codes, {"fb-staffed"})
        total_revenue = sum(
            (Decimal(day["revenue"]) for day in payload["daily_breakdown"]), Decimal("0")
        )
        self.assertEqual(total_revenue, Decimal("100000"))

    def test_spend_on_unstaffed_channel_not_counted(self):
        app = FacebookApp.objects.create(
            name="App", app_id="ads-scope-app", app_secret="s", access_token="t"
        )
        account = FacebookAdAccount.objects.create(
            app=app, name="Acc", account_id="1", fb_id="act_1"
        )
        ChannelAdAccount.objects.create(
            channel=self.ch_noads,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=account.id,
        )
        FacebookAdAccountDailySpend.objects.create(
            ad_account=account, date=date.today(), spend=Decimal("50000")
        )
        payload = self._payload()
        total_cost = sum(
            (Decimal(day["cost"]) for day in payload["daily_breakdown"]), Decimal("0")
        )
        self.assertEqual(total_cost, Decimal("0"))

    def test_assigning_ads_staff_brings_channel_in(self):
        UserChannel.objects.create(user=self.ads, channel=self.ch_noads, is_active=True)
        codes = {row["channel__code"] for row in self._payload()["revenue_by_channel"]}
        self.assertEqual(codes, {"fb-staffed", "fb-unstaffed"})

    def test_revenue_by_channel_zero_fills_spend_only_channel(self):
        # Kênh trong scope, CÓ chi phí ads nhưng 0 đơn chốt: trước đây rớt khỏi
        # revenue_by_channel -> FE (bảng theo kênh + KPI Tổng chi phí) mất phần
        # chi phí này, lệch với khối daily_breakdown vốn cộng đủ.
        ch_spend = Channel.objects.create(code="fb-spend-only", name="FB Spend Only")
        UserChannel.objects.create(user=self.ads, channel=ch_spend, is_active=True)
        app = FacebookApp.objects.create(
            name="App2", app_id="ads-zero-app", app_secret="s", access_token="t"
        )
        account = FacebookAdAccount.objects.create(
            app=app, name="Acc2", account_id="2", fb_id="act_2"
        )
        ChannelAdAccount.objects.create(
            channel=ch_spend,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=account.id,
        )
        FacebookAdAccountDailySpend.objects.create(
            ad_account=account, date=date.today(), spend=Decimal("50000")
        )

        payload = self._payload()
        rows = {row["channel__code"]: row for row in payload["revenue_by_channel"]}
        self.assertEqual(
            rows["fb-spend-only"],
            {
                "channel_id": ch_spend.id,
                "channel__code": "fb-spend-only",
                "channel__name": "FB Spend Only",
                "closed_orders": 0,
                "revenue": "0",
                "delivered_orders": 0,
                "delivered_revenue": "0",
            },
        )
        # Chi phí kênh này vẫn nằm đủ trong daily_breakdown (50000 × 1.1).
        total_cost = sum(
            (Decimal(day["cost"]) for day in payload["daily_breakdown"]), Decimal("0")
        )
        self.assertEqual(total_cost, Decimal("55000"))
        channel_costs = {
            ch["channel__code"]: Decimal(ch["cost"])
            for day in payload["daily_breakdown"]
            for ch in day["channels"]
        }
        self.assertEqual(channel_costs.get("fb-spend-only"), Decimal("55000"))

    def test_channel_filter_limits_zero_fill(self):
        # Bộ lọc kênh của trang phải áp cả lên phần zero-fill.
        ch_idle = Channel.objects.create(code="fb-idle", name="FB Idle")
        UserChannel.objects.create(user=self.ads, channel=ch_idle, is_active=True)

        codes_all = {row["channel__code"] for row in self._payload()["revenue_by_channel"]}
        self.assertEqual(codes_all, {"fb-staffed", "fb-idle"})

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(
            "/api/orders/reports/ads", {"channel_ids": str(self.ch_ads.id)}
        )
        self.assertEqual(response.status_code, 200)
        codes_filtered = {
            row["channel__code"] for row in response.json()["revenue_by_channel"]
        }
        self.assertEqual(codes_filtered, {"fb-staffed"})


class AdsReportTopProductsDeliveredTests(TestCase):
    """Báo cáo ads — top_products có số liệu giao thành công theo từng kênh.

    Bảng "Đơn đã chốt & doanh thu theo kênh" expand ra dòng SKU: cột "Đơn giao
    thành công" và "Doanh thu giao thành công" per-SKU lấy từ
    top_products[].channels — cùng định nghĩa delivered với revenue_by_channel
    (Order.status = 3), doanh thu theo line_total của item.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-ads-top-products")
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
        self.ads = User.objects.create_user(username="ads-top-products-nv", password="x")
        User.objects.filter(pk=self.ads.pk).update(department_id=DepartmentID.ADS)
        self.customer = Customer.objects.create(
            full_name="KH Ads", phone_raw="0900000021", phone_normalized="0900000021"
        )
        # id tường minh tránh đụng SALE_FB_CHANNEL_IDS (sequence không rollback).
        self.channel = Channel.objects.create(
            id=920001, code="fb-top-sp", name="FB Top SP"
        )
        UserChannel.objects.create(user=self.ads, channel=self.channel, is_active=True)
        self.product = Product.objects.create(name="SP Top", sku="SKU-TOP-01")

    def _order_with_item(self, line_total, *, status, quantity=1):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=self.ads,
            name="X",
            phone=self.customer.phone_raw,
            status=status,
            order_source="landing",
            total_after_discount=Decimal(line_total),
        )
        OrderAssessment.objects.create(order=order, sales_status_code="closed")
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=quantity,
            unit_price=Decimal(line_total),
            line_total=Decimal(line_total),
        )
        return order

    def _payload(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/reports/ads")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_top_products_include_delivered_per_channel(self):
        self._order_with_item("100000", status=3, quantity=2)
        self._order_with_item("70000", status=3)
        self._order_with_item("50000", status=0)

        products = self._payload()["top_products"]
        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertEqual(product["closed_orders"], 3)
        self.assertEqual(product["quantity"], 4)
        self.assertEqual(product["revenue"], "220000.00")
        self.assertEqual(product["delivered_orders"], 2)
        self.assertEqual(product["delivered_revenue"], "170000.00")

        self.assertEqual(len(product["channels"]), 1)
        channel = product["channels"][0]
        self.assertEqual(channel["channel__code"], "fb-top-sp")
        self.assertEqual(channel["closed_orders"], 3)
        self.assertEqual(channel["quantity"], 4)
        self.assertEqual(channel["revenue"], "220000.00")
        self.assertEqual(channel["delivered_orders"], 2)
        self.assertEqual(channel["delivered_revenue"], "170000.00")

    def test_no_delivered_orders_returns_zero(self):
        self._order_with_item("100000", status=0)

        product = self._payload()["top_products"][0]
        self.assertEqual(product["delivered_orders"], 0)
        self.assertEqual(product["delivered_revenue"], "0")
        channel = product["channels"][0]
        self.assertEqual(channel["delivered_orders"], 0)
        self.assertEqual(channel["delivered_revenue"], "0")


class AdsReportDuplicateAdAccountCostTests(TestCase):
    """Chi phí theo kênh phải gộp bản sao của cùng một TKQC THẬT.

    ``ChannelAdAccount`` lưu PK, mà mỗi TKQC thật có nhiều dòng
    ``FacebookAdAccount`` (unique ``(app, account_id)``) và mỗi bản sao lại có
    daily spend đầy đủ của cùng số tiền. Hệ quả hai chiều, cả hai đều đã có trên
    prod hoặc suýt có:

    * Hai bản sao của MỘT tài khoản được map vào HAI kênh khác nhau → chi phí
      cộng đôi. Prod 2026-08-19: TKQC 3804581706506110 map bằng pk 127 (app 1)
      vào kênh 42 và pk 55 (app 2) vào kênh 4.
    * Kênh map trúng bản sao KHÔNG có dòng chi tiêu → kênh hiện 0đ dù tài khoản
      thật có tiêu (đây là lớp lỗi đã làm NV ads thấy bảng trắng, MR !1199).
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-ads-dup-cost")
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
        self.ads = User.objects.create_user(username="ads-dup-cost", password="x")
        User.objects.filter(pk=self.ads.pk).update(department_id=DepartmentID.ADS)

        self.ch_a = Channel.objects.create(code="fb-a", name="FB A")
        self.ch_b = Channel.objects.create(code="fb-b", name="FB B")
        for channel in (self.ch_a, self.ch_b):
            UserChannel.objects.create(user=self.ads, channel=channel, is_active=True)

        self.app1 = FacebookApp.objects.create(
            name="App 1", app_id="dup-1", app_secret="s", access_token="t",
        )
        self.app2 = FacebookApp.objects.create(
            name="App 2", app_id="dup-2", app_secret="s", access_token="t",
        )
        # Một TKQC thật, hai dòng DB.
        self.copy1 = FacebookAdAccount.objects.create(
            app=self.app1, name="TK chung", account_id="3804581706506110",
            fb_id="act_3804581706506110",
        )
        self.copy2 = FacebookAdAccount.objects.create(
            app=self.app2, name="TK chung", account_id="3804581706506110",
            fb_id="act_3804581706506110",
        )

    def _map(self, channel, account):
        ChannelAdAccount.objects.create(
            channel=channel,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=account.id,
        )

    def _spend(self, account, amount, day=None):
        FacebookAdAccountDailySpend.objects.create(
            ad_account=account, date=day or date.today(), spend=Decimal(amount),
        )

    def _daily_breakdown(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/reports/ads")
        self.assertEqual(response.status_code, 200)
        return response.json()["daily_breakdown"]

    def _total_cost(self):
        return sum(
            (Decimal(day["cost"]) for day in self._daily_breakdown()), Decimal("0")
        )

    def test_two_copies_on_two_channels_are_not_double_counted(self):
        self._map(self.ch_a, self.copy1)
        self._map(self.ch_b, self.copy2)
        self._spend(self.copy1, "100000")
        self._spend(self.copy2, "100000")
        # 100.000 × 1.1, KHÔNG phải 200.000 × 1.1. Hệ số cũ vì request không lọc
        # ngày → kỳ bao trùm cả quá khứ (xem `facebook_ads_cost_multiplier`).
        self.assertEqual(self._total_cost(), Decimal("110000.0"))

    def _total_cost_for_period(self, day):
        """Tổng chi phí của báo cáo khi LỌC đúng một ngày ``day``."""
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(
            "/api/orders/reports/ads",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        return sum(
            (Decimal(day_row["cost"]) for day_row in response.json()["daily_breakdown"]),
            Decimal("0"),
        )

    def test_period_inside_august_2026_uses_the_reduced_facebook_multiplier(self):
        """Kỳ bắt đầu trong tháng 8/2026 → chi phí FB ×1.08 thay vì ×1.1.

        Ngày viết cứng chứ không dùng ``date.today()``: tháng 8/2026 là giai đoạn
        ×1.08 DUY NHẤT (từ 01/09/2026 hệ số về lại 1.1), nên "hôm nay" không còn
        rơi vào khoảng cần kiểm. Các mốc khác: unit test
        `apps/orders/tests/test_facebook_ads_cost_multiplier.py`.
        """
        self._map(self.ch_a, self.copy1)
        self._spend(self.copy1, "100000", day=date(2026, 8, 10))

        self.assertEqual(
            self._total_cost_for_period(date(2026, 8, 10)), Decimal("108000.0")
        )

    def test_shared_account_cost_is_split_between_its_channels(self):
        self._map(self.ch_a, self.copy1)
        self._map(self.ch_b, self.copy2)
        self._spend(self.copy1, "100000")
        self._spend(self.copy2, "100000")
        by_code = {
            channel["channel__code"]: Decimal(channel["cost"])
            for day in self._daily_breakdown()
            for channel in day["channels"]
        }
        self.assertEqual(by_code["fb-a"], Decimal("55000.0"))
        self.assertEqual(by_code["fb-b"], Decimal("55000.0"))

    def test_spend_hanging_under_an_unmapped_copy_is_still_counted(self):
        """Kênh trỏ bản không có dòng chi tiêu — tiền nằm dưới bản sao của app kia."""
        self._map(self.ch_a, self.copy1)
        self._spend(self.copy2, "100000")
        self.assertEqual(self._total_cost(), Decimal("110000.0"))

    def test_one_copy_mapped_once_is_unchanged(self):
        self._map(self.ch_a, self.copy1)
        self._spend(self.copy1, "100000")
        self._spend(self.copy2, "100000")
        self.assertEqual(self._total_cost(), Decimal("110000.0"))


class AdsReportScopeTests(TestCase):
    """Ads report channel scope theo RBAC scope của section 'ads-report'.

    Trước đây báo cáo ads chỉ mở "xem tất cả kênh" cho role hệ thống (is_superuser).
    Giờ: bất kỳ user nào có scope hiệu dụng của 'ads-report' = ALL đều xem hết kênh —
    tách "xem rộng số liệu ads" khỏi "toàn quyền admin".
    """

    def setUp(self):
        sync_permissions()
        self.ch1 = Channel.objects.create(code="facebook-a", name="FB A")
        self.ch2 = Channel.objects.create(code="facebook-b", name="FB B")

    def _user_with_ads_scope(self, username, scope, *, section_override=False):
        user = User.objects.create_user(username=username, password="x")
        role = Role.objects.create(
            name=f"role-{username}",
            scope_default=DataScope.CHANNEL if section_override else scope,
        )
        RolePermission.objects.bulk_create(
            RolePermission(role=role, permission=p)
            for p in Permission.objects.filter(section="ads-report")
        )
        if section_override:
            RoleSectionScope.objects.create(role=role, section="ads-report", scope=scope)
        UserRoleAssignment.objects.create(user=user, role=role)
        bump_rbac_cache_version()
        return user

    def test_scope_all_sees_all_channels(self):
        user = self._user_with_ads_scope("ads-lead", DataScope.ALL)
        self.assertIsNone(OrderRepository._accessible_ads_channel_ids(user))

    def test_scope_all_via_section_override_sees_all_channels(self):
        # scope_default=CHANNEL nhưng override section ads-report=ALL -> vẫn xem hết.
        user = self._user_with_ads_scope("ads-lead2", DataScope.ALL, section_override=True)
        self.assertIsNone(OrderRepository._accessible_ads_channel_ids(user))

    def test_scope_channel_restricted_to_assigned_channels(self):
        user = self._user_with_ads_scope("ads-scoped", DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch1, is_active=True)
        ids = OrderRepository._accessible_ads_channel_ids(user)
        self.assertEqual(set(ids), {self.ch1.id})

    def test_admin_still_sees_all_channels(self):
        admin = make_admin("admin-ads-scope")
        self.assertIsNone(OrderRepository._accessible_ads_channel_ids(admin))

    def test_none_user_sees_all_channels(self):
        # Caller nội bộ không truyền user -> không giới hạn (backwards compat).
        self.assertIsNone(OrderRepository._accessible_ads_channel_ids(None))


class AdsStaffMultiDepartmentTests(TestCase):
    """NV kiêm vai trò: "là NV Ads" tính theo phòng chính HOẶC phòng ban nhiều (M2M).

    Ca thực tế: lansale là NV Sale (phòng chính Sale để đơn vào báo cáo sale) nhưng
    kiêm chạy ads cho 1 page → thêm Ads vào phòng ban nhiều. Khi đó lansale phải: gán
    được vào kênh, hiện ở ô chọn NV ads, và kênh đó được tính vào báo cáo sale.
    """

    def setUp(self):
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})

    def _user(self, username, primary, extra=()):
        user = User.objects.create_user(username=username, password="x")
        User.objects.filter(pk=user.pk).update(department_id=primary)
        user.refresh_from_db()
        for dept_id in (primary, *extra):
            UserDepartment.objects.get_or_create(
                user=user, department_id=dept_id, defaults={"is_primary": dept_id == primary}
            )
        return user

    def test_user_in_department_fk_and_m2m(self):
        lansale = self._user("lansale", DepartmentID.SALES, extra=(DepartmentID.ADS,))
        self.assertTrue(user_in_department(lansale, DepartmentID.SALES))  # phòng chính (FK)
        self.assertTrue(user_in_department(lansale, DepartmentID.ADS))    # phòng ban nhiều (M2M)

        pure_sale = self._user("puresale", DepartmentID.SALES)
        self.assertFalse(user_in_department(pure_sale, DepartmentID.ADS))

    def test_channel_counted_when_ads_via_m2m(self):
        lansale = self._user("lansale2", DepartmentID.SALES, extra=(DepartmentID.ADS,))
        channel = Channel.objects.create(code="facebook-lan", name="Facebook Lan")
        UserChannel.objects.create(user=lansale, channel=channel, is_active=True)
        self.assertIn(channel.id, set(sale_report_channel_ids()))

    def test_channel_not_counted_when_assignee_not_ads(self):
        sale_only = self._user("puresale2", DepartmentID.SALES)  # không có Ads ở đâu
        channel = Channel.objects.create(code="facebook-x", name="FB X")
        UserChannel.objects.create(user=sale_only, channel=channel, is_active=True)
        self.assertNotIn(channel.id, set(sale_report_channel_ids()))

    def test_staff_picker_includes_ads_via_m2m(self):
        self._user("lansale3", DepartmentID.SALES, extra=(DepartmentID.ADS,))
        usernames = list(
            ChannelRepository.list_staff_users(department=DepartmentID.ADS).values_list(
                "username", flat=True
            )
        )
        self.assertIn("lansale3", usernames)

    def test_staff_picker_excludes_pure_sale_from_ads_list(self):
        self._user("puresale3", DepartmentID.SALES)
        usernames = list(
            ChannelRepository.list_staff_users(department=DepartmentID.ADS).values_list(
                "username", flat=True
            )
        )
        self.assertNotIn("puresale3", usernames)


class _AdsProductFixtureMixin:
    """Fixture dùng chung: 1 kênh, 1 TKQC, 2 chiến dịch (1 đã gán DC30, 1 chưa gán).

    Dùng cho cả đường LỌC theo sản phẩm lẫn đường BUNG theo sản phẩm — hai đường phải
    ra cùng một con số nên buộc phải chạy trên cùng một bộ dữ liệu.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-ads-product-filter")
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
        self.ads = User.objects.create_user(username="ads-product-filter-nv", password="x")
        User.objects.filter(pk=self.ads.pk).update(department_id=DepartmentID.ADS)
        self.customer = Customer.objects.create(
            full_name="KH Ads", phone_raw="0900000031", phone_normalized="0900000031"
        )
        self.channel = Channel.objects.create(id=920011, code="fb-sp", name="FB SP")
        UserChannel.objects.create(user=self.ads, channel=self.channel, is_active=True)

        self.dc30 = Product.objects.create(name="Bồn ngâm chân DC30", sku="DC30")
        self.msc06 = Product.objects.create(name="Máy massage bàn chân", sku="MSC06")

        from apps.facebook_ads.models import FacebookAd, FacebookAdCampaign, FacebookAdDailySpend

        app = FacebookApp.objects.create(
            name="App", app_id="1", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=app, name="TKQC", account_id="77", fb_id="act_77",
        )
        ChannelAdAccount.objects.create(
            channel=self.channel,
            provider=ChannelAdAccount.Provider.FACEBOOK,
            account_id=self.account.id,
        )
        # 1 chiến dịch gán DC30, 1 chiến dịch CHƯA gán — rổ "chưa gán" phải lộ ra.
        assigned = FacebookAdCampaign.objects.create(
            fb_campaign_id="c1", name="LDP_0DC30", ad_account=self.account, product=self.dc30,
        )
        unassigned = FacebookAdCampaign.objects.create(
            fb_campaign_id="c2", name="06/05 bài 3", ad_account=self.account,
        )
        for idx, campaign in ((1, assigned), (2, unassigned)):
            ad = FacebookAd.objects.create(
                fb_ad_id=f"ad{idx}", ad_account=self.account, campaign=campaign,
            )
            FacebookAdDailySpend.objects.create(
                ad=ad, date=date.today(), spend=Decimal("100000"), impressions=1, clicks=1,
            )
        FacebookAdAccountDailySpend.objects.create(
            ad_account=self.account, date=date.today(), spend=Decimal("200000"),
        )

    def _order_with_items(self, items):
        """items = [(product, line_total)] — một đơn có thể chứa nhiều sản phẩm."""
        total = sum(Decimal(line_total) for _, line_total in items)
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=self.ads,
            name="X",
            phone=self.customer.phone_raw,
            status=0,
            order_source="landing",
            total_after_discount=total,
        )
        OrderAssessment.objects.create(order=order, sales_status_code="closed")
        for product, line_total in items:
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                quantity=1,
                unit_price=Decimal(line_total),
                line_total=Decimal(line_total),
            )
        return order

    def _payload(self, **params):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/reports/ads", params)
        self.assertEqual(response.status_code, 200)
        return response.json()


class AdsReportProductFilterTests(_AdsProductFixtureMixin, TestCase):
    """Báo cáo ads lọc theo sản phẩm — nuôi 2 tab "Báo cáo theo digital".

    Hai luật nghiệp vụ (docs/ads-campaign-product-mapping.md):

    * Doanh thu là tiền CỦA RIÊNG sản phẩm trong đơn (line_total), số đơn/số data vẫn
      đếm theo đơn có chứa sản phẩm.
    * Chi phí là chi tiêu THẬT của chiến dịch đã gán về sản phẩm đó, đọc ở grain
      ad/campaign — không phân bổ theo tỉ trọng doanh thu.
    """

    def test_without_filter_nothing_changes(self):
        self._order_with_items([(self.dc30, "300000")])

        day = self._payload()["daily_breakdown"][0]

        self.assertEqual(Decimal(day["revenue"]), Decimal("300000"))
        # Chi phí giữ đường cũ (grain account): 200.000 × 1.1.
        self.assertEqual(Decimal(day["cost"]), Decimal("220000.0"))

    def test_revenue_counts_only_the_filtered_product_lines(self):
        """Đơn mua kèm SP khác chỉ đóng góp phần của SP được lọc."""
        self._order_with_items([(self.dc30, "300000"), (self.msc06, "500000")])

        day = self._payload(product_ids=str(self.dc30.id))["daily_breakdown"][0]

        self.assertEqual(Decimal(day["revenue"]), Decimal("300000"))
        # Số ĐƠN vẫn đếm theo đơn có chứa SP, không chia nhỏ.
        self.assertEqual(day["closed_orders"], 1)
        self.assertEqual(day["leads"], 1)

    def test_cost_comes_from_campaigns_assigned_to_that_product(self):
        self._order_with_items([(self.dc30, "300000")])

        day = self._payload(product_ids=str(self.dc30.id))["daily_breakdown"][0]

        # Chỉ chiến dịch c1 (đã gán DC30): 100.000 × 1.1. Chiến dịch chưa gán KHÔNG
        # được chia sang bất kỳ sản phẩm nào.
        self.assertEqual(Decimal(day["cost"]), Decimal("110000.0"))

    def test_product_burning_money_without_orders_still_shows_up(self):
        """Đơn của SP khác không tính vào doanh thu, nhưng chi phí của SP được lọc VẪN hiện.

        Ẩn dòng này đi là giấu mất đúng thứ phòng Ads cần thấy nhất: sản phẩm đang
        tiêu tiền mà chưa ra đơn nào.
        """
        self._order_with_items([(self.msc06, "500000")])

        days = self._payload(product_ids=str(self.dc30.id))["daily_breakdown"]

        self.assertEqual(len(days), 1)
        self.assertEqual(Decimal(days[0]["revenue"]), Decimal("0"))
        self.assertEqual(Decimal(days[0]["cost"]), Decimal("110000.0"))
        self.assertEqual(days[0]["closed_orders"], 0)

    def test_assigned_cost_exposes_the_unassigned_bucket(self):
        """Tổng − đã gán = rổ "Chưa gán chiến dịch" mà báo cáo digital hiển thị."""
        self._order_with_items([(self.dc30, "300000")])

        payload = self._payload()
        total_cost = sum(Decimal(day["cost"]) for day in payload["daily_breakdown"])
        assigned = Decimal(payload["assigned_ads_cost"])

        self.assertEqual(assigned, Decimal("110000.0"))
        self.assertEqual(total_cost - assigned, Decimal("110000.0"))


class AdsReportProductBreakdownTests(_AdsProductFixtureMixin, TestCase):
    """Bung một dòng digital ra các dòng SẢN PHẨM (endpoint reports/ads/products).

    Bất biến quan trọng nhất: dòng con của SP X phải ra ĐÚNG bằng dòng kênh khi lọc
    ô sản phẩm về X. Lệch ở đây là hai chỗ trên cùng một trang nói hai con số.
    """

    def _rows(self, **params):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/reports/ads/products", params)
        self.assertEqual(response.status_code, 200)
        return response.json()["rows"]

    def test_only_products_assigned_to_a_campaign_get_a_row(self):
        """SP bán được nhưng không có chiến dịch nào thì KHÔNG có dòng (chốt 2026-08-24)."""
        self._order_with_items([(self.dc30, "300000"), (self.msc06, "500000")])

        rows = self._rows()

        self.assertEqual([row["product_id"] for row in rows], [self.dc30.id])
        self.assertEqual(rows[0]["product__sku"], "DC30")
        self.assertEqual(rows[0]["channel_id"], self.channel.id)

    def test_child_row_matches_the_single_product_filter(self):
        self._order_with_items([(self.dc30, "300000"), (self.msc06, "500000")])

        row = self._rows()[0]
        day = self._payload(product_ids=str(self.dc30.id))["daily_breakdown"][0]
        channel = day["channels"][0]

        self.assertEqual(Decimal(row["revenue"]), Decimal(channel["revenue"]))
        self.assertEqual(Decimal(row["cost"]), Decimal(channel["cost"]))
        self.assertEqual(row["closed_orders"], channel["closed_orders"])
        self.assertEqual(row["leads"], channel["leads"])

    def test_children_cost_plus_unassigned_equals_parent(self):
        """Rổ "Chưa gán chiến dịch" = chi phí dòng cha − tổng dòng con, không âm."""
        self._order_with_items([(self.dc30, "300000")])

        parent = self._payload()["daily_breakdown"][0]["channels"][0]
        children_cost = sum(Decimal(row["cost"]) for row in self._rows())

        # Chiến dịch đã gán 100.000 × 1.1; phần còn lại của TKQC là rổ chưa gán.
        self.assertEqual(children_cost, Decimal("110000.0"))
        self.assertEqual(Decimal(parent["cost"]) - children_cost, Decimal("110000.0"))

    def test_product_burning_money_without_orders_still_gets_a_row(self):
        self._order_with_items([(self.msc06, "500000")])

        rows = self._rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product_id"], self.dc30.id)
        self.assertEqual(Decimal(rows[0]["revenue"]), Decimal("0"))
        self.assertEqual(Decimal(rows[0]["cost"]), Decimal("110000.0"))
        self.assertEqual(rows[0]["closed_orders"], 0)
        self.assertEqual(rows[0]["leads"], 0)

    def test_group_day_adds_the_date_dimension(self):
        self._order_with_items([(self.dc30, "300000")])

        by_channel = self._rows()
        by_day = self._rows(group="day")

        self.assertIsNone(by_channel[0]["date"])
        self.assertEqual([row["date"] for row in by_day], [date.today().isoformat()])
        self.assertEqual(Decimal(by_day[0]["cost"]), Decimal(by_channel[0]["cost"]))
        self.assertEqual(Decimal(by_day[0]["revenue"]), Decimal(by_channel[0]["revenue"]))

    def test_product_filter_narrows_the_rows(self):
        self._order_with_items([(self.dc30, "300000")])

        self.assertEqual(self._rows(product_ids=str(self.msc06.id)), [])
        self.assertEqual(len(self._rows(product_ids=str(self.dc30.id))), 1)

    def test_channel_without_ads_staff_is_out_of_scope(self):
        """Cùng phạm vi kênh với phần còn lại của báo cáo ads."""
        UserChannel.objects.filter(user=self.ads, channel=self.channel).update(is_active=False)
        self._order_with_items([(self.dc30, "300000")])

        self.assertEqual(self._rows(), [])
