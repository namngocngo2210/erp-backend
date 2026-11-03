"""Cache + single-flight cho dashboard/report (audit 2026-09-23 C4, perf §2.1).

Bật ``ORDERS_LIST_COUNT_CACHE_TTL`` (mặc định 0 trong test → các test báo cáo hiện
có không đổi hành vi). Chốt ba điều: (1) request thứ hai cùng tham số KHÔNG chạm
`orders` (0 aggregate); (2) ``?refresh=1`` tính lại; (3) báo cáo ads: user bị
giới hạn kênh KHÔNG dùng chung entry với user xem-toàn-bộ (khoá theo scope).
"""
from unittest import mock

import pytest
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.models import DataScope
from apps.users.tests.helpers import make_admin, make_user_with_sections


def _orders_queries(ctx) -> list[str]:
    return [q["sql"] for q in ctx.captured_queries if 'FROM "orders"' in q["sql"]]


@pytest.mark.usefixtures("isolated_cache")
@override_settings(ORDERS_LIST_COUNT_CACHE_TTL=120)
class ReportCacheTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-report-cache")
        channel = Channel.objects.create(code="rc-ch", name="Report Cache Ch")
        customer = Customer.objects.create(
            full_name="KH Report Cache", phone_raw="0909000900", phone_normalized="0909000900"
        )
        Order.objects.create(
            channel=channel, customer=customer, name="RC", phone="0909000900",
            status=3, order_source="landing", total_amount="100000.00",
        )

    def _get(self, path, params=None):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(path, params or {})
        self.assertEqual(response.status_code, 200, response.content[:200])
        return response.json(), ctx

    def test_dashboard_second_request_reads_cache(self):
        self.client.force_authenticate(user=self.admin)
        params = {"date_from": "2026-09-01", "date_to": "2026-09-30"}

        first, ctx1 = self._get("/api/orders/dashboard/summary", params)
        self.assertGreater(len(_orders_queries(ctx1)), 0)

        second, ctx2 = self._get("/api/orders/dashboard/summary", params)
        self.assertEqual(_orders_queries(ctx2), [])
        self.assertEqual(first, second)

        # Khác khoảng ngày → khoá khác → tính lại.
        _, ctx3 = self._get(
            "/api/orders/dashboard/summary", {"date_from": "2026-08-01", "date_to": "2026-08-31"}
        )
        self.assertGreater(len(_orders_queries(ctx3)), 0)

    def test_refresh_param_bypasses_cache(self):
        self.client.force_authenticate(user=self.admin)
        self._get("/api/orders/dashboard/summary")
        _, ctx = self._get("/api/orders/dashboard/summary", {"refresh": "1"})
        self.assertGreater(len(_orders_queries(ctx)), 0)
        # Sau refresh, entry mới đã được ghi lại → request kế đọc cache.
        _, ctx_after = self._get("/api/orders/dashboard/summary")
        self.assertEqual(_orders_queries(ctx_after), [])

    def test_overview_second_request_reads_cache(self):
        self.client.force_authenticate(user=self.admin)
        _, ctx1 = self._get("/api/orders/reports/overview", {"date_from": "2026-09-01"})
        self.assertGreater(len(_orders_queries(ctx1)), 0)
        _, ctx2 = self._get("/api/orders/reports/overview", {"date_from": "2026-09-01"})
        self.assertEqual(_orders_queries(ctx2), [])

    def test_ads_report_cache_is_scoped_per_user_scope(self):
        restricted = make_user_with_sections(
            "ads-restricted-cache", ["ads-report"], scope=DataScope.CHANNEL
        )
        params = {"date_from": "2026-09-01", "date_to": "2026-09-30"}

        # Fixture không có kênh sale nên các summary_ads_* short-circuit không chạm
        # `orders` — đếm số lần TÍNH (compute) thay vì đếm query.
        from apps.orders.transport.views.order_views_impl import dashboard_report_views as views

        with mock.patch.object(
            views, "_compute_ads_report_summary", wraps=views._compute_ads_report_summary
        ) as compute:
            # User bị giới hạn kênh làm ấm cache TRƯỚC (entry riêng theo user id).
            self.client.force_authenticate(user=restricted)
            self._get("/api/orders/reports/ads", params)
            self.assertEqual(compute.call_count, 1)

            # Admin (scope ALL) KHÔNG được ăn entry của user bị giới hạn → tính lại.
            self.client.force_authenticate(user=self.admin)
            self._get("/api/orders/reports/ads", params)
            self.assertEqual(compute.call_count, 2)
            # Admin lần 2 → cache.
            self._get("/api/orders/reports/ads", params)
            self.assertEqual(compute.call_count, 2)

        # Hai scope → hai khoá cache khác nhau (chốt ở tầng khoá, không chỉ ở số lần tính).
        self.assertEqual(views._ads_report_scope(self.admin), "all")
        self.assertEqual(views._ads_report_scope(restricted), f"user:{restricted.id}")

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=0)
    def test_ttl_zero_disables_cache(self):
        self.client.force_authenticate(user=self.admin)
        self._get("/api/orders/dashboard/summary")
        _, ctx = self._get("/api/orders/dashboard/summary")
        self.assertGreater(len(_orders_queries(ctx)), 0)
