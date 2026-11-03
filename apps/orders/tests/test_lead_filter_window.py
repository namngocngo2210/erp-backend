"""Cửa sổ trùng của cổng chặn lead lúc ingest (`LeadFilterService`).

Từ 2026-09-21 cửa sổ là 24 GIỜ TRƯỢT tính từ thời điểm ingest, không còn neo
vào 00:00 hôm nay: lead vào 23:50 rồi vào lại 00:10 vẫn là trùng. Đơn cũ hơn
24h thì cho qua dù vẫn chưa terminal.
"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.customers.services import normalize_phone
from apps.orders.application.services.lead_filter_service import (
    DUPLICATE_WINDOW_HOURS,
    FilterAction,
    LeadFilterService,
)
from apps.orders.models import Order, OrderItem, OrderSource, OrderStatus

PHONE = "0912000777"


class LeadFilterWindowTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="lp-dedup-a", name="Dedup A")
        self.other_channel = Channel.objects.create(code="lp-dedup-b", name="Dedup B")
        self.product = Product.objects.create(name="SP dedup", sku="SKU-DEDUP-A")
        normalized = normalize_phone(PHONE)
        self.customer = Customer.objects.create(
            phone_normalized=normalized,
            phone_raw=PHONE,
            full_name="Khach dedup",
            code=f"CUST-DEDUP-{normalized}",
        )

    def _make_order(self, *, age: timedelta, status=OrderStatus.NEW, channel=None) -> Order:
        order = Order.objects.create(
            channel=channel or self.channel,
            customer=self.customer,
            name="Khach dedup",
            phone=PHONE,
            status=status,
            order_source=OrderSource.LANDING,
            total_amount="100000",
        )
        created_at = timezone.now() - age
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        order.created_at = created_at
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            unit_price="100000",
            line_total="100000",
        )
        return order

    def test_window_is_24_hours(self):
        self.assertEqual(DUPLICATE_WINDOW_HOURS, 24)

    def test_order_23h_ago_is_duplicate_even_across_calendar_day(self):
        # 23h trước gần như luôn rơi sang ngày hôm qua — luật cũ (mốc 00:00) bỏ lọt.
        existing = self._make_order(age=timedelta(hours=23))

        decision = LeadFilterService.evaluate(PHONE, self.channel, product_ids=[self.product.id])

        self.assertEqual(decision.action, FilterAction.REJECT_DUPLICATE)
        self.assertEqual(decision.existing_order_id, existing.id)
        self.assertIn("24 giờ", decision.reason)

    def test_order_25h_ago_is_accepted(self):
        self._make_order(age=timedelta(hours=25))

        decision = LeadFilterService.evaluate(PHONE, self.channel, product_ids=[self.product.id])

        self.assertEqual(decision.action, FilterAction.ACCEPT)

    def test_same_channel_without_product_uses_same_window(self):
        # Không map được sản phẩm → chỉ chặn cùng kênh, vẫn theo cửa sổ 24h.
        self._make_order(age=timedelta(hours=23))

        same_channel = LeadFilterService.evaluate(PHONE, self.channel)
        other_channel = LeadFilterService.evaluate(PHONE, self.other_channel)

        self.assertEqual(same_channel.action, FilterAction.REJECT_DUPLICATE)
        self.assertEqual(other_channel.action, FilterAction.ACCEPT)

    def test_terminal_order_inside_window_is_not_duplicate(self):
        self._make_order(age=timedelta(hours=1), status=OrderStatus.CANCELLED)

        decision = LeadFilterService.evaluate(PHONE, self.channel, product_ids=[self.product.id])

        self.assertEqual(decision.action, FilterAction.ACCEPT)
