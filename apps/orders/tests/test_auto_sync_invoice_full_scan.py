"""auto_sync_invoice_full: quét theo watermark + không tra OrderItem mỗi dòng (audit 2026-09-23 T1 §2.7)."""
from datetime import datetime, timedelta, UTC
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.infrastructure.tasks.order_tasks_impl import auto_sync_invoice_full as task
from apps.orders.models import Order, OrderItem


class ScanSinceTests(TestCase):
    def setUp(self):
        cache.delete(task._LAST_RUN_CACHE_KEY)
        self.now = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)

    @override_settings(AUTO_SYNC_INVOICE_FULL_LOOKBACK_DAYS=7, AUTO_SYNC_INVOICE_FULL_OVERLAP_MINUTES=15)
    def test_lookback_when_no_watermark(self):
        since, source = task._scan_since(self.now)
        self.assertEqual(source, "lookback")
        self.assertEqual(since, self.now - timedelta(days=7))

    @override_settings(AUTO_SYNC_INVOICE_FULL_LOOKBACK_DAYS=7, AUTO_SYNC_INVOICE_FULL_OVERLAP_MINUTES=15)
    def test_watermark_minus_overlap(self):
        task._remember_run(self.now - timedelta(hours=1))
        since, source = task._scan_since(self.now)
        self.assertEqual(source, "watermark")
        self.assertEqual(since, self.now - timedelta(hours=1, minutes=15))

    @override_settings(AUTO_SYNC_INVOICE_FULL_LOOKBACK_DAYS=7, AUTO_SYNC_INVOICE_FULL_OVERLAP_MINUTES=15)
    def test_stale_watermark_clamped_to_lookback(self):
        task._remember_run(self.now - timedelta(days=30))
        since, _ = task._scan_since(self.now)
        self.assertEqual(since, self.now - timedelta(days=7))


class ApplyDetailPriceWithPrefetchTests(TestCase):
    def setUp(self):
        channel = Channel.objects.create(code="sync-pf", name="Sync PF")
        customer = Customer.objects.create(full_name="KH", phone_raw="0900000012", phone_normalized="0900000012")
        self.order = Order.objects.create(channel=channel, customer=customer)
        for i in range(3):
            product = Product.objects.create(name=f"SP{i}", sku=f"SKU-PF-{i}", price=Decimal("1"))
            OrderItem.objects.create(
                order=self.order, product=product, product_name=product.name, sku=product.sku,
                quantity=1, unit_price=Decimal("100"), discount=Decimal("0"), line_total=Decimal("100"),
            )

    def test_prefetched_items_are_used_without_extra_selects(self):
        order = Order.objects.prefetch_related("items").get(pk=self.order.pk)
        details = [{"productCode": f"SKU-PF-{i}", "price": 120} for i in range(3)]
        with CaptureQueriesContext(connection) as ctx:
            changed = [task._apply_invoice_detail_price(order, d) for d in details]
        self.assertEqual(changed, [True, True, True])
        selects = [q["sql"] for q in ctx.captured_queries if q["sql"].startswith("SELECT")]
        self.assertEqual(selects, [], selects)  # 3 UPDATE, 0 SELECT
        self.assertEqual(
            list(OrderItem.objects.filter(order=self.order).values_list("unit_price", flat=True)),
            [Decimal("120")] * 3,
        )
