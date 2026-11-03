"""Test luồng khôi phục đơn đã hủy: task tạo ĐƠN MỚI (clone), đơn cũ giữ nguyên.

Xem spec docs/superpowers/specs/2026-07-28-restore-order-clone-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderItem, OrderStatus, OrderVat
from apps.catalog.models import Product
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.tests.helpers import make_admin

PUSH_EVENT_PATH = (
    "apps.orders.application.services.order_service_helpers."
    "OrderServiceHelpers.push_event"
)


class RestoreOrderTestBase(TestCase):
    def setUp(self):
        self.actor = make_admin("restore-clone-actor")
        self.channel = Channel.objects.create(code="lp-restore", name="Restore Channel")
        self.product = Product.objects.create(name="SP Restore", sku="SKU-RESTORE-01")

    def _make_cancelled_order(self, suffix: str, **overrides) -> Order:
        customer = Customer.objects.create(
            full_name=f"Khach Restore {suffix}",
            phone_raw=f"09266000{suffix}",
            phone_normalized=f"09266000{suffix}",
            code=f"CUST-RES-{suffix}",
        )
        fields = dict(
            channel=self.channel,
            customer=customer,
            name=f"Khach Restore {suffix}",
            phone=f"09266000{suffix}",
            total_amount="200000",
            status=OrderStatus.NEW,
            invoice_status=2,
            invoice_code=f"HD-{suffix}",
            order_code=f"DH-{suffix}",
            shipping_code=f"SHIP-{suffix}",
            shipping_fee="30000",
        )
        fields.update(overrides)
        order = Order.objects.create(**fields)
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=100000,
            line_total=200000,
        )
        return order


class RestoredFromFieldTests(RestoreOrderTestBase):
    def test_restored_from_links_new_order_to_source(self):
        source = self._make_cancelled_order("01")
        clone = self._make_cancelled_order("02", restored_from=source)

        self.assertEqual(clone.restored_from_id, source.id)
        self.assertEqual([o.id for o in source.restored_orders.all()], [clone.id])

    def test_restored_from_is_null_by_default(self):
        order = self._make_cancelled_order("03")

        self.assertIsNone(order.restored_from_id)
        self.assertEqual(order.restored_orders.count(), 0)


class ProcessRestoreOrdersTests(RestoreOrderTestBase):
    """Task khôi phục phải TẠO ĐƠN MỚI và không đụng vào đơn hủy gốc."""

    def _run(self, order_ids, *, show_orders=False):
        from apps.orders.infrastructure.tasks.order_tasks_impl import restore_tasks

        with (
            patch.object(restore_tasks, "_broadcast_restore_completed") as broadcast,
            patch(PUSH_EVENT_PATH) as push_event,
        ):
            result = restore_tasks.process_restore_orders.run(
                order_ids,
                user_id=self.actor.id,
                restore_show_orders=show_orders,
            )
        return result, broadcast, push_event

    def test_creates_new_order_and_leaves_source_untouched(self):
        source = self._make_cancelled_order("10")

        result, _, _ = self._run([source.id])

        self.assertEqual(result["restored_count"], 1)
        new_id = result["restored_ids"][0]
        self.assertNotEqual(new_id, source.id)

        source.refresh_from_db()
        self.assertEqual(source.invoice_status, 2)
        self.assertEqual(source.invoice_code, "HD-10")
        self.assertEqual(source.order_code, "DH-10")
        self.assertEqual(source.shipping_code, "SHIP-10")

        clone = Order.objects.get(pk=new_id)
        self.assertEqual(clone.restored_from_id, source.id)
        self.assertEqual(clone.status, OrderStatus.NEW)
        self.assertIsNone(clone.invoice_status)
        self.assertEqual(clone.invoice_code, "")
        self.assertEqual(clone.order_code, "")
        self.assertEqual(clone.shipping_code, "")
        self.assertEqual(clone.shipping_fee, 0)

    def test_clone_copies_items_and_closed_assessment(self):
        source = self._make_cancelled_order("11")

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        items = list(clone.items.all())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].product_id, self.product.id)
        self.assertEqual(items[0].quantity, 2)
        self.assertEqual(items[0].line_total, 200000)
        self.assertEqual(clone.assessment.sales_status_code, "closed")

    def test_clone_inherits_created_at(self):
        source = self._make_cancelled_order("12")
        source.refresh_from_db()

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        self.assertEqual(clone.created_at, source.created_at)

    def test_show_orders_follows_flag(self):
        hidden_source = self._make_cancelled_order("13")
        shown_source = self._make_cancelled_order("14")

        hidden = self._run([hidden_source.id], show_orders=False)[0]
        shown = self._run([shown_source.id], show_orders=True)[0]

        self.assertFalse(Order.objects.get(pk=hidden["restored_ids"][0]).show_orders)
        self.assertTrue(Order.objects.get(pk=shown["restored_ids"][0]).show_orders)

    def test_copies_business_attributes(self):
        source = self._make_cancelled_order(
            "15",
            vat=OrderVat.COMPANY,
            is_export_vat=True,
            payment_method="cod",
            has_address=False,
        )

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        self.assertEqual(clone.vat, OrderVat.COMPANY)
        self.assertEqual(clone.payment_method, "cod")
        self.assertFalse(clone.has_address)

    def test_does_not_copy_vat_invoice_markers(self):
        """is_export_vat/vat_link là marker "đã xuất hóa đơn" của đơn hủy gốc —
        đơn khôi phục CHƯA xuất hóa đơn nào nên phải về default."""
        source = self._make_cancelled_order(
            "23",
            is_export_vat=True,
            vat_link="https://hoadon.example/xyz",
        )

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        self.assertFalse(clone.is_export_vat)
        self.assertEqual(clone.vat_link, "")

    def test_restored_order_keeps_source_staff(self):
        staff = make_admin("restore-clone-staff")
        source = self._make_cancelled_order("24", staff=staff)

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        self.assertEqual(clone.staff_id, staff.id)

    def test_restored_order_keeps_source_branch_id(self):
        source = self._make_cancelled_order("25", branch_id="BR-KEEP-01")

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        self.assertEqual(clone.branch_id, "BR-KEEP-01")

    def test_second_restore_is_skipped(self):
        source = self._make_cancelled_order("16")

        first, _, _ = self._run([source.id])
        total_after_first = Order.objects.count()
        second, _, _ = self._run([source.id])

        self.assertEqual(first["restored_count"], 1)
        self.assertEqual(second["restored_count"], 0)
        # Đơn đã khôi phục rơi vào already_restored_ids, KHÔNG phải skipped_ids
        # (bấm khôi phục lần hai là thao tác thường gặp, không phải đơn không hợp lệ).
        self.assertEqual(second["skipped_ids"], [])
        self.assertEqual(second["already_restored_ids"], [source.id])
        self.assertEqual(second["already_restored_count"], 1)
        self.assertEqual(Order.objects.count(), total_after_first)

    def test_soft_deleted_clone_allows_restore_again(self):
        source = self._make_cancelled_order("17")

        first, _, _ = self._run([source.id])
        Order.objects.filter(pk=first["restored_ids"][0]).update(
            deleted_at=timezone.now()
        )
        second, _, _ = self._run([source.id])

        self.assertEqual(second["restored_count"], 1)

    def test_order_not_cancelled_is_skipped(self):
        source = self._make_cancelled_order("18", invoice_status=None)

        result, _, _ = self._run([source.id])

        self.assertEqual(result["restored_count"], 0)
        self.assertEqual(result["skipped_ids"], [source.id])

    def test_pos_cancelled_status6_order_is_restored(self):
        """Đơn hủy POS: status=6 nhưng invoice_status vẫn null (KiotViet không set).
        Tab "Đã hủy" hiện các đơn này theo CANCELLED_ORDER_Q nên task cũng phải
        chấp nhận — trước đây chỉ nhận invoice_status=2 khiến đơn hủy POS luôn bị
        báo "không hợp lệ"."""
        source = self._make_cancelled_order(
            "27", invoice_status=None, status=OrderStatus.CANCELLED
        )

        result, _, _ = self._run([source.id])

        self.assertEqual(result["restored_count"], 1)
        self.assertEqual(result["skipped_ids"], [])
        clone = Order.objects.get(pk=result["restored_ids"][0])
        self.assertEqual(clone.status, OrderStatus.NEW)
        self.assertIsNone(clone.invoice_status)

    def test_missing_and_deleted_orders_are_skipped(self):
        deleted = self._make_cancelled_order("19")
        Order.objects.filter(pk=deleted.id).update(deleted_at=timezone.now())

        result, _, _ = self._run([deleted.id, 99999999])

        self.assertEqual(result["restored_count"], 0)
        self.assertEqual(sorted(result["skipped_ids"]), sorted([deleted.id, 99999999]))

    def test_no_order_created_event_is_pushed(self):
        source = self._make_cancelled_order("20")

        _, broadcast, push_event = self._run([source.id])

        push_event.assert_not_called()
        broadcast.assert_called_once()

    def test_broadcast_receives_new_order_id_not_source_id(self):
        """`_broadcast_restore_completed` phải nhận id ĐƠN MỚI (clone), không phải
        id đơn hủy gốc — đây là thay đổi ngữ nghĩa duy nhất so với hành vi UPDATE
        in-place cũ, nơi id không đổi."""
        source = self._make_cancelled_order("26")

        result, broadcast, _ = self._run([source.id])
        new_id = result["restored_ids"][0]

        self.assertNotEqual(new_id, source.id)
        broadcast.assert_called_once_with(
            user_id=self.actor.id,
            restored_ids=[new_id],
            skipped_ids=[],
            already_restored_ids=[],
            failed_ids=[],
        )

    def test_batch_continues_after_one_failure(self):
        """Exception khi clone KHÔNG phải "đơn không hợp lệ": phải rơi vào
        failed_ids để FE báo đúng "lỗi hệ thống" thay vì đổ lỗi cho dữ liệu
        (sự cố 2026-08-03: NotNullViolation bị toast thành "không hợp lệ")."""
        good = self._make_cancelled_order("21")
        bad = self._make_cancelled_order("22")

        from apps.orders.infrastructure.tasks.order_tasks_impl import restore_tasks

        real_duplicate = OrderService.duplicate_order

        def flaky(order, actor, **kwargs):
            if order.id == bad.id:
                raise RuntimeError("boom")
            return real_duplicate(order, actor, **kwargs)

        with (
            patch.object(restore_tasks, "_broadcast_restore_completed"),
            patch(PUSH_EVENT_PATH),
            patch.object(OrderService, "duplicate_order", side_effect=flaky),
        ):
            result = restore_tasks.process_restore_orders.run(
                [bad.id, good.id], user_id=self.actor.id
            )

        self.assertEqual(result["restored_count"], 1)
        self.assertEqual(result["failed_ids"], [bad.id])
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["skipped_ids"], [])
