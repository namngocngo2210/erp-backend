import pytest
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderJourneyEvent
from apps.catalog.models import Product
from apps.users.constants import DepartmentID
from apps.users.models import Department, User
from apps.users.tests.helpers import make_admin


def _setup_sale_departments():
    Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
    Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})


def _as_sales(user):
    """Đưa user vào phòng Sales (báo cáo sale lọc staff__department_id=SALES)."""
    User.objects.filter(pk=user.pk).update(department_id=DepartmentID.SALES)


def _assign_ads_user(channel, username):
    """Tạo 1 NV phòng Ads và gán vào kênh -> kênh nằm trong phạm vi báo cáo sale."""
    ads = User.objects.create_user(username=username, password="x")
    User.objects.filter(pk=ads.pk).update(department_id=DepartmentID.ADS)
    UserChannel.objects.create(user=ads, channel=channel, is_active=True)
    return ads


# Cache report/endpoint: mỗi test một LocMemCache riêng (backend/conftest.py), không cache.clear() Redis dùng chung.
pytestmark = pytest.mark.usefixtures("isolated_cache")


class DashboardSummaryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-dashboard-summary")
        self.staff_alice = User.objects.create_user(
            username="alice", password="x", display_name="Alice"
        )
        self.staff_bob = User.objects.create_user(
            username="bob", password="x", display_name="Bob"
        )
        self.channel = Channel.objects.create(code="lp-a", name="Landing A")
        self.sale_fb_channel = Channel.objects.create(code="sale-fb", name="Sale FB")
        # kênh không gán NV ads -> nằm ngoài phạm vi báo cáo sale
        self.channel_noads = Channel.objects.create(code="misc-noads", name="Misc")
        self.customer = Customer.objects.create(
            full_name="Khach Dashboard",
            phone_raw="0909111222",
            phone_normalized="0909111222",
        )

        _setup_sale_departments()
        _as_sales(self.staff_alice)
        _as_sales(self.staff_bob)
        _assign_ads_user(self.channel, "ads-dashboard-a")

        # Neo mốc vào 12:00 GIỜ ĐỊA PHƯƠNG, không phải timezone.now() thô. Endpoint
        # diễn giải date_from/date_to theo giờ local, còn timezone.now() là UTC —
        # trong khung 00:00-07:00 giờ VN, `.date()` của UTC lùi một ngày so với ngày
        # local, làm đơn "mới nhất" rơi ra ngoài khoảng lọc và test đỏ theo GIỜ CHẠY
        # chứ không theo code (bắt được 2026-07-27 lúc 00:49). Giữa trưa thì lệch
        # timezone không đẩy được mốc sang ngày khác.
        now = timezone.localtime(timezone.now()).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        old_date = now - timedelta(days=6)
        mid_date = now - timedelta(days=2)
        newest_date = now - timedelta(days=1)

        # Landing delivered by Alice (old)
        self.order_old = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            channel_owner="",
            name="Landing Alice Old",
            phone="0909111222",
            status=3,
            order_source="landing",
            staff=self.staff_alice,
            total_amount="100000.00",
        )
        # Landing delivered by Alice (mid)
        self.order_mid = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            channel_owner="",
            name="Landing Alice Mid",
            phone="0909111222",
            status=3,
            order_source="landing",
            staff=self.staff_alice,
            total_amount="200000.00",
        )
        # Landing delivered by Bob (newest)
        self.order_newest = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            channel_owner="",
            name="Landing Bob",
            phone="0909111333",
            status=3,
            order_source="landing",
            staff=self.staff_bob,
            total_amount="300000.00",
        )
        # Landing but not delivered (should be excluded)
        self.order_pending = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            channel_owner="",
            name="Landing Pending",
            phone="0909111444",
            status=1,
            order_source="landing",
            staff=self.staff_alice,
            total_amount="50000.00",
        )
        # Delivered nhưng trên kênh KHÔNG gán NV ads -> ngoài phạm vi báo cáo (excluded)
        self.order_non_landing = Order.objects.create(
            channel=self.channel_noads,
            customer=self.customer,
            channel_owner="",
            name="Non-landing Delivered",
            phone="0909111555",
            status=3,
            order_source=None,
            staff=self.staff_alice,
            total_amount="60000.00",
        )

        Order.objects.filter(id=self.order_old.id).update(created_at=old_date)
        Order.objects.filter(id=self.order_mid.id).update(created_at=mid_date)
        Order.objects.filter(id=self.order_newest.id).update(created_at=newest_date)

    def test_dashboard_summary_without_filter_returns_landing_delivered_by_staff(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        by_staff = {item["staff__username"]: item["total"] for item in payload["landing_delivered_by_staff"]}

        self.assertEqual(by_staff.get("alice"), 2)
        self.assertEqual(by_staff.get("bob"), 1)
        self.assertNotIn("by_status", payload)
        self.assertNotIn("by_channel", payload)

    def test_dashboard_summary_can_filter_by_date_range(self):
        self.client.force_authenticate(user=self.admin)
        # localtime() để khớp cách endpoint diễn giải khoảng ngày (xem setUp).
        local_now = timezone.localtime(timezone.now())
        date_from = (local_now - timedelta(days=3)).date().isoformat()
        date_to = (local_now - timedelta(days=1)).date().isoformat()
        response = self.client.get(
            "/api/orders/dashboard/summary",
            {"date_from": date_from, "date_to": date_to},
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        by_staff = {item["staff__username"]: item["total"] for item in payload["landing_delivered_by_staff"]}

        self.assertEqual(by_staff.get("alice"), 1)
        self.assertEqual(by_staff.get("bob"), 1)

    def test_dashboard_summary_forbidden_without_section_access(self):
        # Default staff (no department) has no `dashboard` section access.
        self.client.force_authenticate(user=self.staff_alice)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 403)


class DashboardCloseRateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-close-rate")
        self.alice = User.objects.create_user(
            username="alice-cr", password="x", display_name="Alice"
        )
        self.bob = User.objects.create_user(
            username="bob-cr", password="x", display_name="Bob"
        )
        self.landing_channel = Channel.objects.create(code="lp-close-rate", name="Landing CR")
        self.sale_fb_channel = Channel.objects.create(code="sale-fb", name="Sale FB")
        self.customer = Customer.objects.create(
            full_name="Khach CloseRate",
            phone_raw="0909222333",
            phone_normalized="0909222333",
        )

        _setup_sale_departments()
        _as_sales(self.alice)
        _as_sales(self.bob)
        # landing_channel có NV ads -> trong phạm vi; sale_fb_channel KHÔNG gán ads
        # -> ngoài phạm vi close-rate (bob chỉ bán sale-fb nên bị loại).
        _assign_ads_user(self.landing_channel, "ads-close-rate")

        # --- Alice on landing channel (order_source=landing) ---
        # 3 closed (closed, closed_express, created_order)
        self._create_order(self.alice, self.landing_channel, sales_status="closed", total=100000, order_source="landing")
        self._create_order(self.alice, self.landing_channel, sales_status="closed_express", total=200000, order_source="landing")
        self._create_order(self.alice, self.landing_channel, sales_status="created_order", total=300000, order_source="landing")
        # 1 spam via sales_status_code
        self._create_order(self.alice, self.landing_channel, sales_status="spam", order_source="landing")
        # 1 spam via customer_classification_code (not in sales_status)
        self._create_order(self.alice, self.landing_channel, classification="spam", order_source="landing")
        # 1 unreachable
        self._create_order(self.alice, self.landing_channel, sales_status="unreachable", order_source="landing")
        # 1 no_answer_1 — counts as unreachable per product spec
        self._create_order(self.alice, self.landing_channel, sales_status="no_answer_1", order_source="landing")
        # 1 neutral (not closed, not spam, not unreachable)
        self._create_order(self.alice, self.landing_channel, sales_status="need_more_consulting", order_source="landing")

        # --- Bob on sale-fb (no landing source): excluded from close-rate metric but included in revenue ---
        self._create_order(self.bob, self.sale_fb_channel, sales_status="closed", total=500000)
        self._create_order(self.bob, self.sale_fb_channel, sales_status="closed_express", total=700000)
        self._create_order(self.bob, self.sale_fb_channel, sales_status="spam")

    def _create_order(self, staff, channel, *, sales_status=None, classification=None, total=0, order_source=None):
        order = Order.objects.create(
            channel=channel,
            customer=self.customer,
            staff=staff,
            name="X",
            phone="0909222333",
            status=0,
            total_after_discount=Decimal(total),
            order_source=order_source,
        )
        if sales_status or classification:
            OrderAssessment.objects.create(
                order=order,
                sales_status_code=sales_status,
                customer_classification_code=classification,
            )
        return order

    def test_close_rate_excludes_sale_fb_and_aggregates_correctly(self):
        # Khách bị bệnh nền: bị loại khỏi close_rate (total giữ nguyên 8) nhưng
        # vẫn nằm trong sale_performance với bucket riêng để FE trừ mẫu số.
        self._create_order(
            self.alice, self.landing_channel, sales_status="underlying_condition", order_source="landing"
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        rows_by_user = {row["staff__username"]: row for row in payload["close_rate_by_staff"]}
        self.assertIn("alice-cr", rows_by_user)
        # Bob only has sale-fb orders → excluded
        self.assertNotIn("bob-cr", rows_by_user)

        alice = rows_by_user["alice-cr"]
        self.assertEqual(alice["total"], 8)
        self.assertEqual(alice["closed"], 3)
        self.assertEqual(alice["spam"], 2)
        self.assertEqual(alice["unreachable"], 2)

        perf_by_user = {row["staff__username"]: row for row in payload["sale_performance_by_staff"]}
        alice_perf = perf_by_user["alice-cr"]
        self.assertEqual(alice_perf["total"], 9)
        self.assertEqual(alice_perf["underlying_condition"], 1)
        self.assertEqual(alice_perf["not_buying"], 0)

    def test_sale_fb_revenue_only_counts_closed_orders_on_sale_fb(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        rows_by_user = {row["staff__username"]: row for row in payload["sale_fb_revenue_by_staff"]}
        self.assertIn("bob-cr", rows_by_user)
        # Alice not on sale-fb → not in this metric
        self.assertNotIn("alice-cr", rows_by_user)

        bob = rows_by_user["bob-cr"]
        self.assertEqual(bob["closed_orders"], 2)
        self.assertEqual(Decimal(bob["revenue"]), Decimal("1200000"))

        self.assertEqual(Decimal(payload["sale_fb_team_revenue"]), Decimal("1200000"))
        self.assertEqual(payload["sale_fb_team_closed_orders"], 2)


class DashboardSalePerformanceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-perf")
        self.alice = User.objects.create_user(
            username="alice-perf", password="x", display_name="Alice"
        )
        self.landing_channel = Channel.objects.create(code="lp-perf", name="Landing Perf")
        self.customer = Customer.objects.create(
            full_name="Khach Perf",
            phone_raw="0909333444",
            phone_normalized="0909333444",
        )

        _setup_sale_departments()
        _as_sales(self.alice)
        _assign_ads_user(self.landing_channel, "ads-perf")

        # Alice — Landing only. Build a deliberate distribution so totals are easy to verify.
        # 2 closed + đã giao (status=3) -> revenue = doanh thu đơn đã giao = 300000.
        self.closed_from_nurture = self._create_order(sales_status="closed", total=100000, status=3)
        OrderJourneyEvent.objects.create(
            order=self.closed_from_nurture,
            event_type="sales_status_changed",
            from_code="need_more_consulting",
            to_code="need_more_consulting",
        )
        self._create_order(sales_status="closed_express", total=200000, status=3)
        # 1 explicit not_buying
        self._create_order(sales_status="not_buying")
        # 2 currently in nurture (1 with prior nurture journey, 1 just landed in nurture)
        currently_nurture = self._create_order(sales_status="scheduled")
        OrderJourneyEvent.objects.create(
            order=currently_nurture,
            event_type="sales_status_changed",
            to_code="scheduled",
        )
        self._create_order(sales_status="call_back_later")
        # 2 unreached (1 no_answer_6, 1 unreachable). no_answer_1..5 vẫn còn
        # nhịp gọi tiếp theo nên KHÔNG tính vào đây.
        self._create_order(sales_status="no_answer_6")
        self._create_order(sales_status="unreachable")
        self._create_order(sales_status="no_answer_3")  # chưa phải mốc cuối
        self._create_order(sales_status="no_answer_4")  # chưa phải mốc cuối
        self._create_order(sales_status="no_answer_5")  # chưa phải mốc cuối
        # 2 dirty (spam + duplicate)
        self._create_order(sales_status="spam")
        self._create_order(sales_status="duplicate")
        # 1 warranty (not dirty, but excluded from reachable)
        self._create_order(sales_status="warranty")
        # 1 with no assessment at all (counts toward total only)
        self._create_order(sales_status=None)

    def _create_order(self, *, sales_status=None, total=0, status=0):
        order = Order.objects.create(
            channel=self.landing_channel,
            customer=self.customer,
            staff=self.alice,
            name="X",
            phone="0909333444",
            status=status,
            total_after_discount=Decimal(total),
            order_source="landing",
        )
        if sales_status:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status)
        return order

    def test_sale_performance_counters_match_status_distribution(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        rows_by_user = {row["staff__username"]: row for row in payload["sale_performance_by_staff"]}
        self.assertIn("alice-perf", rows_by_user)
        alice = rows_by_user["alice-perf"]

        self.assertEqual(alice["total"], 14)
        self.assertEqual(alice["closed"], 2)
        self.assertEqual(alice["not_buying"], 1)
        self.assertEqual(alice["nurture"], 2)
        # Per spec, only no_answer_6 + unreachable count as "không tiếp cận được"
        self.assertEqual(alice["unreached"], 2)
        self.assertEqual(alice["dirty"], 2)
        self.assertEqual(alice["warranty"], 1)
        self.assertEqual(Decimal(alice["revenue"]), Decimal("300000"))

    def test_closed_revenue_excludes_scheduled_date_and_cancelled(self):
        """Doanh thu chốt (Nhóm 9): tính cả đơn sau đó bị hoàn, KHÔNG tính đơn
        đã huỷ (loại ở _base_orders_queryset) và KHÔNG tính "đã chốt, hẹn ngày".
        `closed` (Nhóm 1) vẫn tính đủ 4 trạng thái — hai con số cố ý lệch nhau."""
        # Đơn đã chốt rồi bị hoàn (status=5) -> VẪN tính vào doanh thu chốt.
        self._create_order(sales_status="closed_express", total=50000, status=5)
        # Đơn đã chốt nhưng đã huỷ (status=6) -> KHÔNG tính.
        self._create_order(sales_status="created_order", total=70000, status=6)
        # Đơn "đã chốt, hẹn ngày" -> KHÔNG tính vào doanh thu chốt, NHƯNG vẫn nằm
        # trong `closed` của Nhóm 1.
        self._create_order(sales_status="closed_scheduled_date", total=40000)
        # Đơn chưa chốt dù có tiền -> KHÔNG tính.
        self._create_order(sales_status="need_more_consulting", total=90000)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        alice = next(
            row
            for row in payload["sale_performance_by_staff"]
            if row["staff__username"] == "alice-perf"
        )

        # setUp: 100.000 (closed) + 200.000 (closed_express) + 50.000 đơn chốt bị hoàn.
        # 40.000 của đơn hẹn ngày KHÔNG được cộng vào.
        self.assertEqual(Decimal(alice["closed_revenue"]), Decimal("350000"))
        # Tiền đi qua JSON dưới dạng chuỗi để không mất độ chính xác Decimal.
        self.assertIsInstance(alice["closed_revenue"], str)
        # 3 đơn sinh ra số tiền trên (2 từ setUp + 1 đơn hoàn).
        self.assertEqual(alice["closed_revenue_orders"], 3)
        # Nhóm 1 vẫn tính "đã chốt, hẹn ngày" -> nhiều hơn đúng 1 đơn.
        self.assertEqual(alice["closed"], 4)
        # Nhóm 4 (chỉ đơn giao thành công) không bị ảnh hưởng.
        self.assertEqual(Decimal(alice["revenue"]), Decimal("300000"))

    def test_wants_store_visit_is_excluded_from_capacity_buckets(self):
        """"Khách muốn đến cửa hàng": khách tự ra cửa hàng chốt nên kết quả nằm
        ngoài tầm sale online — có bucket riêng để FE trừ khỏi mẫu số Nhóm 1 và
        Nhóm 2, KHÔNG nằm trong rổ nurture của Nhóm 2 và không cộng doanh thu."""
        self._create_order(sales_status="wants_store_visit", total=60000)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        alice = next(
            row
            for row in response.json()["sale_performance_by_staff"]
            if row["staff__username"] == "alice-perf"
        )

        # Vẫn đếm vào `total` / `total_assessed` — FE trừ ra bằng bucket riêng,
        # để số bị loại còn đối chiếu được trên giao diện.
        self.assertEqual(alice["total"], 15)
        self.assertEqual(alice["wants_store_visit"], 1)
        # setUp có 2 đơn đang nuôi; đơn "đến cửa hàng" KHÔNG cộng thêm vào đó.
        self.assertEqual(alice["nurture"], 2)
        # Không đụng vào các rổ đã chốt / doanh thu.
        self.assertEqual(alice["closed"], 2)
        self.assertEqual(Decimal(alice["closed_revenue"]), Decimal("300000"))

    def test_wants_store_visit_drops_out_of_close_rate_rows(self):
        """Endpoint `close_rate_by_staff` loại hẳn đơn "đến cửa hàng" khỏi
        `total`, khớp mẫu số Nhóm 1 mà FE tự dựng từ `sale_performance_by_staff`
        (total − underlying_condition − wants_store_visit)."""
        self._create_order(sales_status="wants_store_visit", total=60000)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        payload = response.json()
        close_rate = next(
            row for row in payload["close_rate_by_staff"] if row["staff__username"] == "alice-perf"
        )
        perf = next(
            row
            for row in payload["sale_performance_by_staff"]
            if row["staff__username"] == "alice-perf"
        )

        self.assertEqual(close_rate["total"], 14)
        self.assertEqual(
            close_rate["total"],
            perf["total"] - perf["underlying_condition"] - perf["wants_store_visit"],
        )

    def test_follow_up_conversion_uses_journey_history(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        payload = response.json()
        alice = next(
            row for row in payload["sale_performance_by_staff"] if row["staff__username"] == "alice-perf"
        )
        # 2 orders ever passed through nurture (the closed-from-nurture one + the one currently scheduled)
        self.assertEqual(alice["nurture_total_ever"], 2)
        # Of those, only 1 ended up closed
        self.assertEqual(alice["nurture_followup_closed"], 1)

    def test_delivery_quality_by_staff_classifies_orders(self):
        # Build delivery scenarios on top of the existing setUp orders. Setup-only
        # orders all have status=0 (NEW) so they don't count as "sent".
        # Add a fresh batch with explicit statuses to make the math obvious.
        bob = User.objects.create_user(
            username="bob-perf", password="x", display_name="Bob"
        )
        _as_sales(bob)

        def make(status, *, days_ago=0):
            order = Order.objects.create(
                channel=self.landing_channel,
                customer=self.customer,
                staff=bob,
                name="X",
                phone="0909333444",
                status=status,
                order_source="landing",
            )
            if days_ago > 0:
                Order.objects.filter(id=order.id).update(
                    created_at=timezone.now() - timedelta(days=days_ago)
                )
            return order

        # 5 sent (statuses 2,3,4,5,7); 1 of them is RETURNED (5), 1 DELIVERED (3)
        make(2)
        make(3)
        make(4)
        make(5)
        make(7)
        # 1 not-sent, NEW
        make(0)
        # 2 pending > 7 days (status 2 = IN_DELIVERY, > 7 days old)
        make(2, days_ago=10)
        make(11, days_ago=8)
        # 1 pending but only 3 days old (should NOT count as tồn)
        make(2, days_ago=3)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        payload = response.json()
        rows = {row["staff__username"]: row for row in payload["delivery_quality_by_staff"]}
        bob_row = rows["bob-perf"]
        self.assertEqual(bob_row["total"], 9)
        # Sent = statuses 2,3,4,5,7 + the days_ago=10 (status 2) + days_ago=8 (status 11)
        # + days_ago=3 (status 2) = 8 sent total. Only the NEW (status=0) is not sent.
        self.assertEqual(bob_row["sent"], 8)
        # "returned" nuôi ô KPI tổng của Nhóm 7 nên gồm cả rổ đang hoàn:
        # status 4 (đang chuyển hoàn) + status 5 (đã chuyển hoàn) = 2.
        self.assertEqual(bob_row["returned"], 2)
        self.assertEqual(bob_row["delivered"], 1)
        # Pending > 7 days: the 2 explicitly-aged orders
        self.assertEqual(bob_row["pending_over_threshold"], 2)
        # Nhóm 5a: đang giao = 3 đơn status 2 + status 7 + status 0 + status 11 = 6.
        # Bom = status 4 + status 5 = 2.
        self.assertEqual(bob_row["in_delivery"], 6)
        self.assertEqual(bob_row["bomb"], 2)
        # 3 rổ rời nhau và phủ hết tổng data.
        self.assertEqual(
            bob_row["in_delivery"] + bob_row["bomb"] + bob_row["delivered"],
            bob_row["total"],
        )

    def test_bomb_reasons_aggregated_with_label(self):
        # Mark 3 orders RETURNED with different bomb reasons.
        for code in ("changed_mind_not_buying", "changed_mind_not_buying", "delivery_too_slow"):
            order = Order.objects.create(
                channel=self.landing_channel,
                customer=self.customer,
                staff=self.alice,
                name="X",
                phone="0909333444",
                status=5,  # RETURNED
                order_source="landing",
            )
            OrderAssessment.objects.create(
                order=order, sales_status_code="closed", bomb_reason_type_code=code
            )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        payload = response.json()
        reasons = payload["bomb_reasons"]
        by_code = {r["assessment__bomb_reason_type_code"]: r for r in reasons}
        self.assertEqual(by_code["changed_mind_not_buying"]["count"], 2)
        self.assertEqual(by_code["changed_mind_not_buying"]["label"], "Khách đổi ý/không còn nhu cầu")
        self.assertEqual(by_code["delivery_too_slow"]["count"], 1)

    def test_return_rate_by_product_groups_by_product(self):
        # Mỗi đơn gắn 1 sản phẩm qua OrderItem. % hoàn theo SP = đơn hoàn / đơn đã gửi.
        product_a = Product.objects.create(name="Ghế massage A", sku="SKU-A")
        product_b = Product.objects.create(name="Ghế massage B", sku="SKU-B")
        product_c = Product.objects.create(name="Ghế massage C", sku="SKU-C")

        def make(status, product):
            order = Order.objects.create(
                channel=self.landing_channel,
                customer=self.customer,
                staff=self.alice,
                name="X",
                phone="0909333444",
                status=status,
                order_source="landing",
            )
            if product is not None:
                OrderItem.objects.create(order=order, product=product)
            return order

        make(5, product_a)   # đã hoàn (status 5) + đã gửi — SP A
        make(3, product_a)   # giao TC (đã gửi) — SP A
        make(5, product_b)   # đã hoàn + đã gửi — SP B
        make(0, product_b)   # mới (chưa gửi) — SP B
        make(5, None)        # đã hoàn nhưng KHÔNG có item → loại khỏi báo cáo SP
        # SP C: cả 3 trạng thái ĐANG hoàn đều phải vào tử số, không chỉ status 5.
        make(4, product_c)   # đang chuyển hoàn
        make(12, product_c)  # chờ chuyển hoàn lại
        make(13, product_c)  # chờ xác nhận hoàn
        make(3, product_c)   # giao TC — chỉ vào mẫu số

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rows = {r["items__product__sku"]: r for r in payload["return_rate_by_product"]}

        self.assertEqual(rows["SKU-A"]["total"], 2)
        self.assertEqual(rows["SKU-A"]["sent"], 2)
        self.assertEqual(rows["SKU-A"]["returned"], 1)

        self.assertEqual(rows["SKU-B"]["total"], 2)
        self.assertEqual(rows["SKU-B"]["sent"], 1)
        self.assertEqual(rows["SKU-B"]["returned"], 1)

        self.assertEqual(rows["SKU-C"]["total"], 4)
        self.assertEqual(rows["SKU-C"]["sent"], 4)
        self.assertEqual(rows["SKU-C"]["returned"], 3)

        # Đơn không có item không tạo thêm nhóm SP nào.
        self.assertEqual(set(rows.keys()), {"SKU-A", "SKU-B", "SKU-C"})

    def test_data_quality_by_channel_aggregates_per_channel(self):
        # Add a second channel with its own distribution to verify per-channel split.
        other_channel = Channel.objects.create(code="lp-other", name="Landing Other")
        _assign_ads_user(other_channel, "ads-other")
        other_order = Order.objects.create(
            channel=other_channel,
            customer=self.customer,
            staff=self.alice,
            name="X",
            phone="0909333444",
            status=0,
            order_source="landing",
        )
        OrderAssessment.objects.create(order=other_order, sales_status_code="spam")

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        payload = response.json()
        rows_by_code = {row["channel__code"]: row for row in payload["data_quality_by_channel"]}

        primary = rows_by_code["lp-perf"]
        self.assertEqual(primary["total"], 14)
        self.assertEqual(primary["unreached"], 2)
        self.assertEqual(primary["dirty"], 2)

        other = rows_by_code["lp-other"]
        self.assertEqual(other["total"], 1)
        self.assertEqual(other["unreached"], 0)
        self.assertEqual(other["dirty"], 1)


class DashboardChannelZeroFillTests(TestCase):
    """Kênh trong phạm vi báo cáo sale nhưng 0 đơn trong kỳ vẫn phải hiện dòng 0.

    Trước đây 2 bảng theo kênh (chất lượng data, hiệu quả giao hàng) group từ
    queryset đơn hàng nên kênh không có đơn/doanh thu trong kỳ biến mất khỏi
    báo cáo; giờ được zero-fill. Kênh KHÔNG gán NV Ads vẫn nằm ngoài báo cáo.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-zero-fill")
        _setup_sale_departments()
        self.sale = User.objects.create_user(username="sale-zf", password="x")
        _as_sales(self.sale)
        self.customer = Customer.objects.create(
            full_name="KH ZeroFill", phone_raw="0900000111", phone_normalized="0900000111"
        )

        # Kênh có NV ads + có đơn trong kỳ
        self.ch_active = Channel.objects.create(code="zf-active", name="ZF Active")
        _assign_ads_user(self.ch_active, "ads-zf-active")
        # Kênh có NV ads nhưng KHÔNG có đơn nào -> phải hiện dòng 0
        self.ch_idle = Channel.objects.create(code="zf-idle", name="ZF Idle")
        _assign_ads_user(self.ch_idle, "ads-zf-idle")
        # Kênh không gán ads -> ngoài phạm vi, không được zero-fill
        self.ch_noads = Channel.objects.create(code="zf-noads", name="ZF NoAds")

        Order.objects.create(
            channel=self.ch_active,
            customer=self.customer,
            staff=self.sale,
            name="X",
            phone="0900000111",
            status=3,
            order_source="landing",
        )

    def _summary(self, **params):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary", params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_idle_channel_appears_with_zero_rows(self):
        payload = self._summary()

        quality = {r["channel__code"]: r for r in payload["data_quality_by_channel"]}
        self.assertEqual(quality["zf-active"]["total"], 1)
        self.assertEqual(
            quality["zf-idle"],
            {
                "channel_id": self.ch_idle.id,
                "channel__code": "zf-idle",
                "channel__name": "ZF Idle",
                "total": 0,
                "unreached": 0,
                "dirty": 0,
            },
        )
        self.assertNotIn("zf-noads", quality)

        delivery = {r["channel__code"]: r for r in payload["delivery_quality_by_channel"]}
        self.assertEqual(delivery["zf-active"]["total"], 1)
        self.assertEqual(delivery["zf-idle"]["total"], 0)
        self.assertEqual(delivery["zf-idle"]["sent"], 0)
        self.assertEqual(delivery["zf-idle"]["returned"], 0)
        self.assertEqual(delivery["zf-idle"]["delivered"], 0)
        self.assertEqual(delivery["zf-idle"]["pending_over_threshold"], 0)
        self.assertNotIn("zf-noads", delivery)

    def test_channel_with_orders_outside_range_still_listed_as_zero(self):
        date_from = (timezone.now() + timedelta(days=2)).date().isoformat()
        date_to = (timezone.now() + timedelta(days=3)).date().isoformat()
        payload = self._summary(date_from=date_from, date_to=date_to)

        quality = {r["channel__code"]: r for r in payload["data_quality_by_channel"]}
        self.assertEqual(quality["zf-active"]["total"], 0)
        self.assertEqual(quality["zf-idle"]["total"], 0)

    def test_zero_rows_sorted_after_active_channels_by_name(self):
        payload = self._summary()
        codes = [r["channel__code"] for r in payload["delivery_quality_by_channel"]]
        self.assertEqual(codes, ["zf-active", "zf-idle"])


class DashboardAdsChannelScopeTests(TestCase):
    """Phạm vi báo cáo sale = kênh CÓ gán NV phòng Ads (thay whitelist id cứng).

    Khóa hành vi mới: đơn của NV sale chỉ vào báo cáo khi kênh của đơn có một NV
    phòng Ads (còn active, không phải admin) đang gán qua UserChannel. Gỡ gán ->
    kênh rời khỏi báo cáo; kênh chưa gán ads / Resale / KOC không được tính.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-ads-scope")
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})

        self.sale = User.objects.create_user(username="sale1", password="x", display_name="Sale 1")
        User.objects.filter(pk=self.sale.pk).update(department_id=DepartmentID.SALES)
        self.ads = User.objects.create_user(username="ads1", password="x", display_name="Ads 1")
        User.objects.filter(pk=self.ads.pk).update(department_id=DepartmentID.ADS)

        self.customer = Customer.objects.create(
            full_name="KH Scope", phone_raw="0900000000", phone_normalized="0900000000"
        )

        # Kênh CÓ gán NV ads -> trong phạm vi báo cáo
        self.ch_ads = Channel.objects.create(code="facebook-x", name="Facebook X")
        UserChannel.objects.create(user=self.ads, channel=self.ch_ads, is_active=True)
        # Kênh KHÔNG gán ads -> ngoài phạm vi (dù staff là sale)
        self.ch_noads = Channel.objects.create(code="facebook-y", name="Facebook Y")
        # Kênh Resale (code sale-fb, không gán ads) -> ngoài phạm vi
        self.ch_resale = Channel.objects.create(code="sale-fb", name="Resale")

        # đơn của sale trên từng kênh
        self._order(self.ch_ads, "closed")
        self._order(self.ch_ads, "created_order")
        self._order(self.ch_ads, "not_buying")
        self._order(self.ch_noads, "closed")  # phải bị loại (kênh chưa gán ads)
        self._order(self.ch_resale, "closed")  # phải bị loại (Resale)

    def _order(self, channel, sales_status):
        order = Order.objects.create(
            channel=channel,
            customer=self.customer,
            staff=self.sale,
            name="X",
            phone="0900000000",
            status=0,
            order_source="landing",
        )
        OrderAssessment.objects.create(order=order, sales_status_code=sales_status)
        return order

    def _perf_row(self):
        self.client.force_authenticate(user=self.admin)
        payload = self.client.get("/api/orders/dashboard/summary").json()
        rows = {r["staff__username"]: r for r in payload["sale_performance_by_staff"]}
        return rows

    def test_sale_report_channel_ids_only_active_non_admin_ads(self):
        from apps.orders._repositories_reports import sale_report_channel_ids

        self.assertEqual(set(sale_report_channel_ids()), {self.ch_ads.id})

    def test_only_ads_assigned_channel_counts(self):
        row = self._perf_row()["sale1"]
        # chỉ 3 đơn trên ch_ads được tính; ch_noads + ch_resale bị loại
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["closed"], 2)  # closed + created_order

    def test_removing_ads_staff_excludes_channel(self):
        from apps.channels_core.infrastructure.repositories.channel_repositories_impl import (
            ChannelRepository,
        )

        ChannelRepository.remove_staff_from_channel(channel=self.ch_ads, user=self.ads)
        self.isolated_cache.clear()  # LocMem cục bộ — bỏ kết quả đã cache trước khi gỡ gán
        self.assertNotIn("sale1", self._perf_row())

    def test_admin_ads_user_does_not_bring_channel_into_scope(self):
        from apps.orders._repositories_reports import sale_report_channel_ids
        from apps.users.models import DataScope, Role, UserRoleAssignment

        # ch_noads chỉ có 1 user ads NHƯNG là admin (role hệ thống) -> không tính
        sys_role, _ = Role.objects.get_or_create(
            name="Administrator", defaults={"is_system": True, "scope_default": DataScope.ALL}
        )
        UserRoleAssignment.objects.get_or_create(user=self.ads, role=sys_role)
        UserChannel.objects.create(user=self.ads, channel=self.ch_noads, is_active=True)
        self.assertNotIn(self.ch_noads.id, set(sale_report_channel_ids()))


# ══════════════════════════════════════════════════════════════════════════════
# Pure pytest — chuỗi "không nghe máy" phải nhất quán giữa catalog và các rổ báo
# cáo. Thêm mốc mới (lần 7...) mà quên cập nhật hằng số là lỗi lọc IM LẶNG: đơn
# rơi ra ngoài mọi rổ, tổng các rổ không còn khớp tổng data.
# ══════════════════════════════════════════════════════════════════════════════


def _no_answer_codes():
    from apps.orders.crm_catalog import SALES_STATUS_OPTIONS

    return [o.code for o in SALES_STATUS_OPTIONS if o.code.startswith("no_answer_")]


def test_no_answer_catalog_is_a_contiguous_sequence():
    """Catalog phải là dãy liên tục 1..N, đúng thứ tự — UI dựng dropdown theo thứ tự này."""
    codes = _no_answer_codes()
    assert codes == [f"no_answer_{i}" for i in range(1, len(codes) + 1)]
    assert len(codes) == 6


def test_every_no_answer_code_lands_in_the_unreachable_bucket():
    from apps.orders._repositories_reports import UNREACHABLE_SALES_STATUS_CODES

    assert set(_no_answer_codes()) <= set(UNREACHABLE_SALES_STATUS_CODES)


def test_only_the_last_no_answer_code_counts_as_unreached():
    """Nhóm 3 ("không tiếp cận được") chỉ tính mốc gọi CUỐI — các mốc trước vẫn
    còn nhịp gọi tiếp theo nên chưa được coi là mất liên lạc."""
    from apps.orders._repositories_reports import UNREACHED_SALES_STATUS_CODES

    codes = _no_answer_codes()
    assert set(codes) & set(UNREACHED_SALES_STATUS_CODES) == {codes[-1]}


def test_dead_sales_statuses_track_the_last_no_answer_code():
    """Sheet 3 (Tư vấn) chỉ đẩy đơn ra ở mốc gọi cuối — trùng mốc của Nhóm 3."""
    from apps.orders.sheet_rules import DEAD_SALES_STATUSES

    codes = _no_answer_codes()
    assert set(codes) & DEAD_SALES_STATUSES == {codes[-1]}


def test_lark_reminder_covers_every_no_answer_code():
    from apps.orders.infrastructure.tasks.order_tasks_impl.sale_reminder_tasks import (
        NO_ANSWER_CODES,
    )

    assert set(_no_answer_codes()) == set(NO_ANSWER_CODES)


def test_contact_rules_chain_every_no_answer_code_to_an_action():
    """Mỗi mốc phải suy ra được tác nghiệp; mốc cuối mới là "check lại số"."""
    from apps.orders.domain.contact_rules import ACTION_KEYS, derive_action_needed

    codes = _no_answer_codes()
    for code in codes[:-1]:
        action = derive_action_needed(code, 0, False)
        assert action in ACTION_KEYS and action != "none"
    assert derive_action_needed(codes[-1], 0, False) == "recheck_phone"
