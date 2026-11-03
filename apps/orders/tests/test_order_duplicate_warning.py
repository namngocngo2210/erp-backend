"""Test luật cảnh báo trùng đơn hiển thị trên các trang danh sách.

Luật chốt 2026-08-21 (khác LeadFilterService — bên đó CHẶN lead lúc ingest với
cửa sổ 24h trượt; bên này chỉ CẢNH BÁO hiển thị, cửa sổ 30 ngày):
hai đơn khớp nhau khi cùng phone_normalized + giao nhau ≥1 sản phẩm +
created_at cách nhau ≤ 30 ngày. Đơn khớp được xếp vào MỘT trong hai rổ theo
trạng thái của chính nó:
- `shipping` (SHIPPING_STATUSES) → "đã có đơn đang vận chuyển"
- `returning` (RETURN_STATUSES) → "đang có đơn hoàn"
Đơn huỷ (6) không vào rổ nào và cũng không được cảnh báo.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.customers.services import normalize_phone
from apps.orders.application.services.order_duplicate_warning_service import (
    MAX_DUPLICATE_IDS,
    DuplicateWarning,
    RETURN_STATUSES,
    SHIPPING_STATUSES,
    build_duplicate_map,
)
from apps.orders.models import Order, OrderItem, OrderSource, OrderStatus
from apps.catalog.models import Product


class OrderDuplicateWarningTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="lp-dupwarn", name="Dup Warn")
        self.product = Product.objects.create(name="SP A", sku="SKU-DW-A")
        self.other_product = Product.objects.create(name="SP B", sku="SKU-DW-B")
        self.now = timezone.now()

    def _customer_for(self, phone: str) -> Customer:
        normalized = normalize_phone(phone)
        customer, _ = Customer.objects.get_or_create(
            phone_normalized=normalized,
            defaults={
                "full_name": "Khach DW",
                "phone_raw": phone,
                "code": f"CUST-DW-{normalized}",
            },
        )
        return customer

    def _make_order(
        self,
        *,
        phone="0912000111",
        status=OrderStatus.NEW,
        product=None,
        created_at=None,
        deleted_at=None,
    ) -> Order:
        order = Order.objects.create(
            channel=self.channel,
            customer=self._customer_for(phone),
            name="Khach DW",
            phone=phone,
            status=status,
            order_source=OrderSource.LANDING,
            total_amount="100000",
            deleted_at=deleted_at,
        )
        if created_at is not None:
            Order.objects.filter(pk=order.pk).update(created_at=created_at)
            order.created_at = created_at
        OrderItem.objects.create(
            order=order,
            product=product or self.product,
            product_name=(product or self.product).name,
            quantity=1,
            unit_price="100000",
            line_total="100000",
        )
        return order

    # ── Luật cơ bản ────────────────────────────────────────────────
    def test_same_phone_same_product_flags_both_orders(self):
        first = self._make_order()
        second = self._make_order()

        result = build_duplicate_map([first, second])

        self.assertEqual(result[first.id].shipping, [second.id])
        self.assertEqual(result[second.id].shipping, [first.id])

    def test_same_phone_different_product_is_not_duplicate(self):
        first = self._make_order()
        second = self._make_order(product=self.other_product)

        result = build_duplicate_map([first, second])

        self.assertEqual(result, {})

    def test_different_product_is_not_a_return_warning_either(self):
        """Anh chốt 21/08: cảnh báo hoàn cũng phải trùng sản phẩm."""
        first = self._make_order()
        self._make_order(status=OrderStatus.IN_RETURN, product=self.other_product)

        self.assertEqual(build_duplicate_map([first]), {})

    def test_different_phone_is_not_duplicate(self):
        first = self._make_order(phone="0912000111")
        second = self._make_order(phone="0912000222")

        self.assertEqual(build_duplicate_map([first, second]), {})

    def test_normalized_phone_matches_across_formats(self):
        """+84912000111 và 0912000111 là cùng một số."""
        first = self._make_order(phone="0912000111")
        second = self._make_order(phone="+84912000111")

        result = build_duplicate_map([first, second])

        self.assertEqual(result[first.id].shipping, [second.id])

    # ── Rổ vận chuyển ──────────────────────────────────────────────
    def test_delivered_success_still_counts_as_duplicate(self):
        """Anh chốt: đơn đã Giao thành công vẫn là đơn trùng (trong 30 ngày)."""
        first = self._make_order()
        second = self._make_order(status=OrderStatus.DELIVERED_SUCCESS)

        self.assertEqual(build_duplicate_map([first])[first.id].shipping, [second.id])

    def test_every_shipping_status_lands_in_the_shipping_bucket(self):
        first = self._make_order()
        for status in SHIPPING_STATUSES:
            with self.subTest(status=status):
                other = self._make_order(status=status)
                warning = build_duplicate_map([first])[first.id]
                self.assertEqual(warning.shipping, [other.id])
                self.assertEqual(warning.returning, [])
                other.delete()

    # ── Rổ hoàn ────────────────────────────────────────────────────
    def test_every_return_status_lands_in_the_return_bucket(self):
        first = self._make_order()
        for status in RETURN_STATUSES:
            with self.subTest(status=status):
                other = self._make_order(status=status)
                warning = build_duplicate_map([first])[first.id]
                self.assertEqual(warning.returning, [other.id])
                self.assertEqual(warning.shipping, [])
                other.delete()

    def test_return_and_shipping_warnings_coexist(self):
        page_order = self._make_order()
        shipping = self._make_order(status=OrderStatus.IN_DELIVERY)
        returning = self._make_order(status=OrderStatus.IN_RETURN)

        warning = build_duplicate_map([page_order])[page_order.id]

        self.assertEqual(warning.shipping, [shipping.id])
        self.assertEqual(warning.returning, [returning.id])

    def test_warning_is_symmetric_between_shipping_and_return(self):
        """Ca thực tế 21/08: đơn Mới thấy đơn hoàn, đơn hoàn thấy đơn Mới."""
        fresh = self._make_order(status=OrderStatus.NEW)
        returning = self._make_order(status=OrderStatus.IN_RETURN)

        result = build_duplicate_map([fresh, returning])

        self.assertEqual(result[fresh.id].returning, [returning.id])
        self.assertEqual(result[fresh.id].shipping, [])
        self.assertEqual(result[returning.id].shipping, [fresh.id])
        self.assertEqual(result[returning.id].returning, [])

    # ── Đơn huỷ ────────────────────────────────────────────────────
    def test_cancelled_counterpart_is_ignored(self):
        first = self._make_order()
        self._make_order(status=OrderStatus.CANCELLED)

        self.assertEqual(build_duplicate_map([first]), {})

    def test_cancelled_page_order_is_not_flagged(self):
        """Đơn đã huỷ hiển thị trên trang thì không tô cảnh báo, dù khách có đơn khác."""
        cancelled = self._make_order(status=OrderStatus.CANCELLED)
        self._make_order()

        self.assertEqual(build_duplicate_map([cancelled]), {})

    def test_soft_deleted_order_is_ignored(self):
        first = self._make_order()
        self._make_order(deleted_at=self.now)

        self.assertEqual(build_duplicate_map([first]), {})

    # ── Cửa sổ 30 ngày ─────────────────────────────────────────────
    def test_order_within_window_is_duplicate(self):
        first = self._make_order()
        second = self._make_order(created_at=self.now - timedelta(days=29))

        self.assertEqual(build_duplicate_map([first])[first.id].shipping, [second.id])

    def test_order_outside_window_is_not_duplicate(self):
        first = self._make_order()
        self._make_order(created_at=self.now - timedelta(days=31))

        self.assertEqual(build_duplicate_map([first]), {})

    def test_return_warning_uses_the_same_window(self):
        """Anh chốt 21/08: cảnh báo hoàn dùng chung cửa sổ 30 ngày."""
        first = self._make_order()
        self._make_order(status=OrderStatus.IN_RETURN, created_at=self.now - timedelta(days=31))

        self.assertEqual(build_duplicate_map([first]), {})

    def test_window_is_relative_between_the_two_orders(self):
        """Xem lại đơn cũ vẫn thấy cảnh báo — cửa sổ tính giữa 2 đơn, không neo hôm nay."""
        old_first = self._make_order(created_at=self.now - timedelta(days=200))
        old_second = self._make_order(created_at=self.now - timedelta(days=190))

        result = build_duplicate_map([old_first])

        self.assertEqual(result[old_first.id].shipping, [old_second.id])

    # ── Biên ───────────────────────────────────────────────────────
    def test_duplicate_ids_are_capped_and_newest_first(self):
        page_order = self._make_order(created_at=self.now - timedelta(days=20))
        others = [
            self._make_order(created_at=self.now - timedelta(days=day))
            for day in range(1, MAX_DUPLICATE_IDS + 3)
        ]

        ids = build_duplicate_map([page_order])[page_order.id].shipping

        self.assertEqual(len(ids), MAX_DUPLICATE_IDS)
        newest_first = [o.id for o in sorted(others, key=lambda o: o.created_at, reverse=True)]
        self.assertEqual(ids, newest_first[:MAX_DUPLICATE_IDS])

    def test_each_bucket_is_capped_on_its_own(self):
        page_order = self._make_order(created_at=self.now - timedelta(days=20))
        for day in range(1, MAX_DUPLICATE_IDS + 3):
            self._make_order(created_at=self.now - timedelta(days=day))
            self._make_order(
                status=OrderStatus.IN_RETURN, created_at=self.now - timedelta(days=day)
            )

        warning = build_duplicate_map([page_order])[page_order.id]

        self.assertEqual(len(warning.shipping), MAX_DUPLICATE_IDS)
        self.assertEqual(len(warning.returning), MAX_DUPLICATE_IDS)

    def test_order_without_items_has_no_duplicate(self):
        bare = Order.objects.create(
            channel=self.channel,
            customer=self._customer_for("0912000111"),
            name="Khach DW",
            phone="0912000111",
            status=OrderStatus.NEW,
            order_source=OrderSource.LANDING,
        )
        self._make_order()

        self.assertEqual(build_duplicate_map([bare]), {})

    def test_empty_page_runs_no_query(self):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(build_duplicate_map([]), {})
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_single_query_regardless_of_page_size(self):
        page = [self._make_order(phone=f"09120001{i:02d}") for i in range(10)]
        for order in page:
            self._make_order(phone=order.phone)

        with CaptureQueriesContext(connection) as ctx:
            result = build_duplicate_map(page)

        self.assertEqual(len(ctx.captured_queries), 1)
        self.assertEqual(len(result), len(page))


class OrderDuplicateWarningSerializerTests(SimpleTestCase):
    """Hai rổ phải ra hai field riêng, và CHỈ ở payload list.

    `duplicate_order_ids` giữ nguyên tên cũ (nay = rổ vận chuyển) để khỏi phải
    đụng vào mọi call site; `returning_order_ids` là field mới cho rổ hoàn.
    """

    def test_two_buckets_map_to_two_fields(self):
        from apps.orders.transport.serializers.order_serializers_impl.order_core_serializers import (  # noqa: E501
            OrderListSerializer,
        )

        warning = DuplicateWarning(shipping=[11, 12], returning=[21])
        serializer = OrderListSerializer(context={"duplicate_map": {7: warning}})
        order = SimpleNamespace(id=7)

        self.assertEqual(serializer.get_duplicate_order_ids(order), [11, 12])
        self.assertEqual(serializer.get_returning_order_ids(order), [21])

    def test_missing_context_yields_empty_lists(self):
        """Serialize many=True thiếu context không được thành N+1 hay nổ KeyError."""
        from apps.orders.transport.serializers.order_serializers_impl.order_core_serializers import (  # noqa: E501
            OrderListSerializer,
        )

        serializer = OrderListSerializer(context={})
        order = SimpleNamespace(id=7)

        self.assertEqual(serializer.get_duplicate_order_ids(order), [])
        self.assertEqual(serializer.get_returning_order_ids(order), [])

    def test_full_order_serializer_has_neither_field(self):
        """Bẫy đã cắn 18/08: field rỗng ở response PATCH xoá cảnh báo khỏi bảng."""
        from apps.orders.transport.serializers.order_serializers_impl.order_core_serializers import (  # noqa: E501
            OrderListSerializer,
            OrderSerializer,
        )

        self.assertNotIn("duplicate_order_ids", OrderSerializer().fields)
        self.assertNotIn("returning_order_ids", OrderSerializer().fields)
        self.assertIn("duplicate_order_ids", OrderListSerializer().fields)
        self.assertIn("returning_order_ids", OrderListSerializer().fields)
