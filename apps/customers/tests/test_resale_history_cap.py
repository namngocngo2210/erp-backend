"""Lịch sử mua trên dòng resale có trần theo TỪNG khách (mở từ audit 2026-08-19, đóng 2026-09-23).

Không có LIMIT thì khách mua nhiều kéo toàn bộ đơn + items + product cho mỗi dòng
của trang. Trần áp bằng ROW_NUMBER() OVER (PARTITION BY customer) trong SQL nên
khách đứng sau trong `customer_ids` không bị mất lịch sử như LIMIT tổng.
"""
from unittest import mock

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.customer_resale_service import CustomerResaleService
from apps.customers.models import Customer
from apps.orders.models import Order


class ResaleHistoryCapTests(TestCase):
    def test_history_capped_per_customer_newest_first(self):
        channel = Channel.objects.create(code="rh-ch", name="RH")
        customers = [
            Customer.objects.create(full_name=f"KH {i}", phone_raw=f"090000008{i}", phone_normalized=f"090000008{i}")
            for i in range(2)
        ]
        for customer in customers:
            for i in range(5):
                Order.objects.create(
                    channel=channel, customer=customer, name="O", phone=customer.phone_raw,
                    order_code=f"OC-{customer.id}-{i}", total_amount=i,
                )

        with mock.patch.object(CustomerResaleService, "RESALE_HISTORY_MAX_ORDERS_PER_CUSTOMER", 3):
            history = CustomerResaleService._resale_order_history_by_customer(
                [c.id for c in customers]
            )

        for customer in customers:
            rows = history[customer.id]
            self.assertEqual(len(rows), 3)
            # Mới nhất trước: created_at tăng theo thứ tự tạo → đơn i=4,3,2 còn lại.
            self.assertEqual([r["order_code"] for r in rows], [f"OC-{customer.id}-4", f"OC-{customer.id}-3", f"OC-{customer.id}-2"])

    def test_default_cap_keeps_all_when_under_limit(self):
        channel = Channel.objects.create(code="rh-ch2", name="RH2")
        customer = Customer.objects.create(full_name="KH", phone_raw="0900000099", phone_normalized="0900000099")
        for i in range(4):
            Order.objects.create(channel=channel, customer=customer, name="O", phone="0900000099")
        history = CustomerResaleService._resale_order_history_by_customer([customer.id])
        self.assertEqual(len(history[customer.id]), 4)
