"""Snapshot mốc vòng đời đơn (spec 2026-08-04-order-milestone-snapshots).

Hai mốc write-once theo (order, milestone): ``created`` ghi lúc ingest,
``invoiced`` ghi lúc invoice_code được gán lần đầu. Sửa đơn về sau không đổi
snapshot; gọi trùng không tạo dòng mới.
"""
from decimal import Decimal

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.milestone_snapshot_service import (
    capture_invoice_snapshot,
)
from apps.orders.models import Order, OrderMilestone, OrderMilestoneSnapshot
from apps.catalog.models import Product
from apps.orders.application.services.order_service_impl import OrderService


class CreatedSnapshotTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-ms", name="FB MS", owner="FB MS")

    def _ingest(self, phone, **extra):
        return OrderService.create_order_from_landing(
            {"channel_id": self.channel.id, "phone": phone, **extra}
        )

    def test_ingest_writes_created_snapshot_with_price_book_source(self):
        Product.objects.create(name="Bồn ngâm chân", sku="BNC11", price="1500000")
        order = self._ingest("0912000001", product_id="BNC11", quantity=2)
        snap = order.milestone_snapshots.get(milestone=OrderMilestone.CREATED)
        # 2 × 1.500.000 − 200k giảm giá tự động của dòng SL 2.
        self.assertEqual(snap.total_amount, Decimal("2800000"))
        self.assertEqual(snap.quantity, 2)
        self.assertEqual(snap.sku, "BNC11")
        self.assertEqual(snap.amount_source, "price_book")

    def test_ingest_text_and_payload_sources(self):
        o1 = self._ingest("0912000002", product="Mua 1 Thảm: 1.290.000 VNĐ")
        self.assertEqual(
            o1.milestone_snapshots.get(milestone="created").amount_source, "text"
        )
        o2 = self._ingest("0912000003", total_amount=2550000)
        self.assertEqual(
            o2.milestone_snapshots.get(milestone="created").amount_source, "payload"
        )
        o3 = self._ingest("0912000004")
        self.assertEqual(
            o3.milestone_snapshots.get(milestone="created").amount_source, "none"
        )

    def test_clone_copies_created_snapshot_amounts(self):
        Product.objects.create(name="Tủ", sku="SQA11", price="1500000")
        order = self._ingest("0912000005", product_id="SQA11", quantity=1)
        clone = OrderService.duplicate_order(order, None)
        snap = clone.milestone_snapshots.get(milestone="created")
        self.assertEqual(snap.total_amount, Decimal("1500000"))
        self.assertEqual(snap.amount_source, "cloned")


class InvoiceSnapshotTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-ms2", name="FB MS2")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0912000010", phone_normalized="0912000010"
        )

    def _order(self, **kwargs):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0912000010",
            **kwargs,
        )

    def test_capture_invoice_snapshot_freezes_current_values(self):
        order = self._order(
            total_amount=Decimal("4200000"),
            total_after_discount=Decimal("4200000"),
            quantity=3,
            sku="MSC06",
            invoice_code="HD-1",
        )
        capture_invoice_snapshot(order)
        order.total_after_discount = Decimal("9999999")
        order.save()
        snap = order.milestone_snapshots.get(milestone=OrderMilestone.INVOICED)
        self.assertEqual(snap.total_after_discount, Decimal("4200000"))
        self.assertEqual(snap.amount_source, "invoice")

    def test_capture_invoice_snapshot_idempotent(self):
        order = self._order(total_after_discount=Decimal("1000"), invoice_code="HD-2")
        capture_invoice_snapshot(order)
        order.total_after_discount = Decimal("2000")
        order.save()
        capture_invoice_snapshot(order)
        self.assertEqual(OrderMilestoneSnapshot.objects.filter(order=order).count(), 1)
        self.assertEqual(
            order.milestone_snapshots.get(milestone="invoiced").total_after_discount,
            Decimal("1000"),
        )


class MilestoneSnapshotApiTests(TestCase):
    """GET /api/orders/{id}/milestone-snapshots — cùng gate với journey-events."""

    def setUp(self):
        from rest_framework.test import APIClient

        from apps.users.tests.helpers import make_admin

        self.client = APIClient()
        self.admin = make_admin("admin-ms-api")
        self.channel = Channel.objects.create(code="fb-ms3", name="FB MS3", owner="FB MS3")

    def test_returns_snapshots_for_order(self):
        Product.objects.create(name="Đai lưng", sku="DTL11", price="1200000")
        order = OrderService.create_order_from_landing(
            {"channel_id": self.channel.id, "phone": "0912000020", "product_id": "DTL11"}
        )
        order.invoice_code = "HD-API-1"
        capture_invoice_snapshot(order)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{order.id}/milestone-snapshots")
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([r["milestone"] for r in results], ["created", "invoiced"])
        self.assertEqual(Decimal(results[0]["total_amount"]), Decimal("1200000"))
        self.assertEqual(results[0]["amount_source"], "price_book")

    def test_requires_order_visibility(self):
        from apps.users.models import User

        order = OrderService.create_order_from_landing(
            {"channel_id": self.channel.id, "phone": "0912000021"}
        )
        outsider = User.objects.create_user(username="outsider-ms", password="x")
        self.client.force_authenticate(user=outsider)
        response = self.client.get(f"/api/orders/{order.id}/milestone-snapshots")
        self.assertNotEqual(response.status_code, 200)


class SeedCommandTests(TestCase):
    """seed_order_milestone_snapshots — suy ngược một lần cho đơn có trước bảng."""

    def setUp(self):
        from apps.customers.models import Customer

        self.channel = Channel.objects.create(code="fb-ms4", name="FB MS4")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0912000030", phone_normalized="0912000030"
        )

    def _order(self, **kwargs):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0912000030",
            order_source="landing",
            **kwargs,
        )

    def _run(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("seed_order_milestone_snapshots", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        self._order(landing_confirmed_amount=Decimal("1500000"))
        output = self._run()
        self.assertEqual(OrderMilestoneSnapshot.objects.count(), 0)
        self.assertIn("DRY-RUN", output)

    def test_apply_seeds_created_and_invoiced(self):
        order = self._order(
            landing_confirmed_amount=Decimal("1500000"),
            total_after_discount=Decimal("4200000"),
            invoice_code="HD-SEED-1",
        )
        self._run("--apply")
        created = order.milestone_snapshots.get(milestone="created")
        self.assertEqual(created.total_amount, Decimal("1500000"))
        self.assertEqual(created.amount_source, "seeded")
        invoiced = order.milestone_snapshots.get(milestone="invoiced")
        self.assertEqual(invoiced.total_after_discount, Decimal("4200000"))
        self.assertEqual(invoiced.amount_source, "seeded")

    def test_apply_idempotent_and_never_overwrites_real_snapshot(self):
        order = self._order(
            landing_confirmed_amount=Decimal("1500000"), invoice_code="HD-SEED-2"
        )
        OrderMilestoneSnapshot.objects.create(
            order=order,
            milestone=OrderMilestone.CREATED,
            total_amount=Decimal("999000"),
            amount_source="price_book",
        )
        self._run("--apply")
        self._run("--apply")
        self.assertEqual(OrderMilestoneSnapshot.objects.filter(order=order).count(), 2)
        created = order.milestone_snapshots.get(milestone="created")
        self.assertEqual(created.total_amount, Decimal("999000"))
        self.assertEqual(created.amount_source, "price_book")

    def test_skips_orders_without_signal(self):
        # không landing_confirmed_amount, không invoice_code -> không seed gì
        self._order()
        self._run("--apply")
        self.assertEqual(OrderMilestoneSnapshot.objects.count(), 0)
