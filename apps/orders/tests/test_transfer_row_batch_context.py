"""Batch context cho row realtime của transfer Landing → Đơn hàng (audit 2026-08-19 P0.1).

Trước fix: ``transfer_landing_orders`` gọi ``build_order_created_row(order)`` từng
đơn trong vòng lặp; mỗi row tự dựng ``build_duplicate_map`` 1-phần-tử + query
reminder riêng ≈ 8 query × 50 đơn trong một POST.

Hai hợp đồng khoá ở đây:
1. Serialize CẢ LÔ với ``build_order_rows_context`` = **0 query phụ trội** khi
   queryset đã nạp đủ quan hệ (đúng bộ select/prefetch mà view dùng) — hợp đồng
   này bất biến theo cỡ lô, mạnh hơn assertNumQueries đếm số cứng.
2. Row qua đường batch GIỐNG HỆT row qua đường 1-đơn (đường ingest/duplicate vẫn
   dùng) — đổi shape payload WS là vỡ zero-REST broadcast phía FE.
"""

from django.apps import apps as django_apps
from django.db.models import Prefetch
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_event_publisher import (
    build_order_created_row,
    build_order_rows_context,
)
from apps.orders.models import Order, OrderNeed
from apps.orders.tests.helpers import make_scoped_user
from apps.users.models import DataScope


def _loaded_orders(ids):
    """Nạp đơn với đúng bộ quan hệ mà transfer_landing_orders nạp cho lô trong trần."""
    return list(
        Order.objects.filter(id__in=ids)
        .select_related(
            "channel",
            "kiotviet_channel",
            "customer",
            "staff",
            "assessment",
            "assessment__updated_by",
            "recall",
        )
        .prefetch_related(
            "items",
            "recall_entries",
            Prefetch(
                "assessment__needs",
                queryset=OrderNeed.objects.select_related("need_type__group").order_by("id"),
            ),
        )
    )


class TransferRowBatchContextTests(TestCase):
    def setUp(self):
        kiotviet_channel_model = django_apps.get_model("kiotviet", "KiotVietChannel")
        self.kv = kiotviet_channel_model.objects.create(
            name="KV Row", retailer="desi-row", username="u", password="p"
        )
        self.channel = Channel.objects.create(code="row-ch", name="Row CH")
        self.customer = Customer.objects.create(
            full_name="Khach Row", phone_raw="0909333444", phone_normalized="0909333444"
        )
        self.staff = make_scoped_user("row-staff", "orders", scope=DataScope.ALL)
        self.orders = [
            Order.objects.create(
                channel=self.channel,
                kiotviet_channel=self.kv,
                customer=self.customer,
                staff=self.staff,
                name=f"Don ROW-{i}",
                phone="0909333444",
                total_amount="50000.00",
                invoice_code=f"HD-ROW-{i}",
                invoice_id=7100 + i,
                order_source="landing",
            )
            for i in range(4)
        ]

    def test_batch_context_serializes_with_zero_extra_queries(self):
        orders = _loaded_orders([o.id for o in self.orders])
        context = build_order_rows_context(orders)

        with self.assertNumQueries(0):
            rows = [build_order_created_row(order, context) for order in orders]

        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row is not None for row in rows))

    def test_batch_row_identical_to_single_row_path(self):
        orders = _loaded_orders([o.id for o in self.orders[:2]])
        context = build_order_rows_context(orders)

        for order in orders:
            batched = build_order_created_row(order, context)
            single = build_order_created_row(order)
            self.assertEqual(batched, single)
