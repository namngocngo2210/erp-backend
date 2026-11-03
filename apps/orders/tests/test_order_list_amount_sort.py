"""Danh sách đơn: sắp xếp theo "Tổng sau giảm giá".

`sort=amount_desc|amount_asc` chạy trên MỌI endpoint list dùng `apply_orders_sort`.
(Dòng tổng dưới bảng chỉ cộng các dòng đang hiển thị — tính ở FE, không cần API.)
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.tests.helpers import make_admin


class OrdersAmountSortTests(TestCase):
    # (endpoint, order_source) — order_source khớp phần endpoint khoá cứng.
    LIST_ENDPOINTS = (
        ("/api/orders", "landing"),
        ("/api/orders/koc", "koc"),
        ("/api/orders/landing", "landing"),
    )

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-orders-amount")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="lp-orders-amount", name="Orders Amount")
        self.customer = Customer.objects.create(
            full_name="Khach Amount",
            phone_raw="0909333004",
            phone_normalized="0909333004",
        )

    def _order(self, amount: str, *, order_source="landing", **extra) -> Order:
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name=f"amount-{amount}",
            order_source=order_source,
            total_after_discount=Decimal(amount),
            **extra,
        )

    def _post(self, endpoint: str, **body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_amount_sort_both_directions_on_every_list_page(self):
        for endpoint, order_source in self.LIST_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                mid = self._order("200000", order_source=order_source)
                high = self._order("900000", order_source=order_source)
                low = self._order("50000", order_source=order_source)
                mine = {low.id, mid.id, high.id}

                desc = [r["id"] for r in self._post(endpoint, sort="amount_desc")["results"]]
                asc = [r["id"] for r in self._post(endpoint, sort="amount_asc")["results"]]

                self.assertEqual([i for i in desc if i in mine], [high.id, mid.id, low.id])
                self.assertEqual([i for i in asc if i in mine], [low.id, mid.id, high.id])
                Order.objects.filter(id__in=mine).delete()

    def test_equal_amounts_tie_break_by_id_in_sort_direction(self):
        first = self._order("100000")
        second = self._order("100000")
        desc = [r["id"] for r in self._post("/api/orders", sort="amount_desc")["results"]]
        self.assertEqual(desc, [second.id, first.id])
