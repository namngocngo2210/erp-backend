"""Loại khách (Customer.is_new_customer): cũ = đã có ≥1 đơn GIAO THÀNH CÔNG, mới = chưa.

Đơn tính = chưa xóa mềm, status=DELIVERED_SUCCESS, hóa đơn không bị hủy.
Cover: event ``orders.saved`` khi tạo đơn + đổi trạng thái (full save lẫn
update_fields), soft delete từng đơn, xoá cứng (post_delete), bulk delete API
(queryset.update + gọi thẳng refresh), reconcile toàn cục, và field customer_is_new
trong OrderSerializer.

Event phát qua ``publish_on_commit`` nên trong ``TestCase`` (transaction bọc, không
commit) phải ``captureOnCommitCallbacks(execute=True)`` quanh mỗi ``.save()`` — helper
``_order``/``_save`` làm việc đó; test nào save tay mà không qua helper thì cờ KHÔNG đổi.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.customer_type import reconcile_customer_type, refresh_customer_type
from apps.customers.models import Customer
from apps.orders.models import Order, OrderStatus
from apps.users.tests.helpers import make_admin


class CustomerTypeTestBase(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="ct-ch", name="CT Ch", owner="CT Ch")
        self.customer = Customer.objects.create(
            full_name="KH loại khách",
            phone_raw="0911000001",
            phone_normalized="0911000001",
        )

    def _order(self, **kwargs):
        defaults = dict(
            channel=self.channel,
            customer=self.customer,
            name="Đơn test",
            phone="0911000001",
            status=OrderStatus.NEW,
            order_source="landing",
        )
        defaults.update(kwargs)
        with self.captureOnCommitCallbacks(execute=True):
            return Order.objects.create(**defaults)

    def _save(self, order, **kwargs):
        """``order.save(**kwargs)`` và chạy luôn callback on_commit (event orders.saved)."""
        with self.captureOnCommitCallbacks(execute=True):
            order.save(**kwargs)

    def _delivered(self, **kwargs):
        kwargs.setdefault("status", OrderStatus.DELIVERED_SUCCESS)
        return self._order(**kwargs)

    def _is_new(self, customer=None):
        target = customer or self.customer
        target.refresh_from_db()
        return target.is_new_customer


class CustomerTypeSignalTests(CustomerTypeTestBase):
    def test_default_true_without_orders(self):
        self.assertTrue(self._is_new())

    def test_first_delivered_order_flips_to_old(self):
        self._delivered()
        self.assertFalse(self._is_new())

    def test_non_delivered_orders_do_not_count(self):
        # Nhiều đơn nhưng chưa đơn nào giao thành công → vẫn khách mới.
        self._order(status=OrderStatus.NEW)
        self._order(status=OrderStatus.IN_DELIVERY)
        self._order(status=OrderStatus.CANCELLED)
        self.assertTrue(self._is_new())

    def test_delivered_with_cancelled_invoice_does_not_count(self):
        # KiotViet hủy-xuất lại hóa đơn: đơn cũ giữ status=3 nhưng invoice_status=2
        # — không được tính là đơn giao thành công.
        self._delivered(invoice_status=2)
        self.assertTrue(self._is_new())

    def test_status_transition_via_full_save_flips_to_old(self):
        # Webhook upsert save full object (update_fields=None).
        order = self._order(status=OrderStatus.IN_DELIVERY)
        self.assertTrue(self._is_new())
        order.status = OrderStatus.DELIVERED_SUCCESS
        self._save(order)
        self.assertFalse(self._is_new())

    def test_status_transition_via_update_fields_flips_to_old(self):
        order = self._order(status=OrderStatus.IN_DELIVERY)
        order.status = OrderStatus.DELIVERED_SUCCESS
        self._save(order, update_fields=["status", "updated_at"])
        self.assertFalse(self._is_new())

    def test_unrelated_update_fields_skips_recompute(self):
        # save chỉ đụng field ngoài phép đếm → không đổi cờ (dù DB đang lệch chủ ý).
        order = self._delivered()
        Customer.objects.filter(id=self.customer.id).update(is_new_customer=True)
        order.feedback = "ghi chú"
        self._save(order, update_fields=["feedback", "updated_at"])
        self.assertTrue(self._is_new())

    def test_soft_delete_via_save_restores_new(self):
        order = self._delivered()
        self.assertFalse(self._is_new())
        # Giống DELETE /api/orders/<id>: soft delete qua instance.save(update_fields=...).
        order.deleted_at = timezone.now()
        self._save(order, update_fields=["deleted_at", "updated_at"])
        self.assertTrue(self._is_new())

    def test_hard_delete_restores_new(self):
        order = self._delivered()
        self.assertFalse(self._is_new())
        # post_delete vẫn là signal (không qua on_commit) — chạy ngay trong transaction.
        order.delete()
        self.assertTrue(self._is_new())

    def test_queryset_hard_delete_restores_new(self):
        # QuerySet.delete() không gọi Order.delete() nhưng bắn post_delete từng dòng —
        # lý do giữ signal thay vì override Order.delete() (xem customers/subscribers.py).
        self._delivered()
        self.assertFalse(self._is_new())
        Order.objects.filter(customer=self.customer).delete()
        self.assertTrue(self._is_new())

    def test_recompute_waits_for_commit(self):
        # Event phát qua publish_on_commit: chưa commit thì cờ chưa đổi; rollback thì
        # không recompute (consumer chỉ thấy state đã commit).
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            Order.objects.create(
                channel=self.channel,
                customer=self.customer,
                name="Đơn chờ commit",
                phone="0911000001",
                status=OrderStatus.DELIVERED_SUCCESS,
                order_source="landing",
            )
            self.assertTrue(self._is_new())
        self.assertTrue(callbacks)
        for callback in callbacks:
            callback()
        self.assertFalse(self._is_new())


class RefreshAndReconcileTests(CustomerTypeTestBase):
    def test_refresh_fixes_stale_flag_both_directions(self):
        self._delivered()
        Customer.objects.filter(id=self.customer.id).update(is_new_customer=True)
        self.assertEqual(refresh_customer_type([self.customer.id]), 1)
        self.assertFalse(self._is_new())

        Order.objects.filter(customer=self.customer).update(deleted_at=timezone.now())
        self.assertEqual(refresh_customer_type([self.customer.id]), 1)
        self.assertTrue(self._is_new())

    def test_refresh_ignores_empty_and_none(self):
        self.assertEqual(refresh_customer_type([]), 0)
        self.assertEqual(refresh_customer_type([None]), 0)

    def test_reconcile_fixes_all_drift(self):
        other = Customer.objects.create(
            full_name="KH khác", phone_raw="0911000002", phone_normalized="0911000002"
        )
        self._delivered()
        self._order(customer=other, status=OrderStatus.IN_DELIVERY)
        # Làm lệch cả hai chiều bằng update trực tiếp (bypass signal).
        Customer.objects.filter(id=self.customer.id).update(is_new_customer=True)
        Customer.objects.filter(id=other.id).update(is_new_customer=False)
        self.assertEqual(reconcile_customer_type(), 2)
        self.assertFalse(self._is_new())
        self.assertTrue(self._is_new(other))

    def test_reconcile_after_queryset_status_update(self):
        # queryset.update đổi status bypass signal → reconcile phải vá được.
        order = self._order(status=OrderStatus.IN_DELIVERY)
        Order.objects.filter(id=order.id).update(status=OrderStatus.DELIVERED_SUCCESS)
        self.assertTrue(self._is_new())  # signal không bắt được
        reconcile_customer_type()
        self.assertFalse(self._is_new())


class CustomerTypeApiTests(CustomerTypeTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = make_admin("admin-customer-type")
        self.client.force_authenticate(user=self.admin)

    def _list_rows(self):
        response = self.client.post(
            "/api/orders", {"limit": "100", "offset": "0"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"]

    def test_order_list_exposes_customer_is_new(self):
        self._order(status=OrderStatus.IN_DELIVERY)
        rows = self._list_rows()
        self.assertTrue(rows)
        self.assertIn("customer_is_new", rows[0])
        self.assertTrue(rows[0]["customer_is_new"])

        self._delivered()
        rows = self._list_rows()
        self.assertTrue(all(row["customer_is_new"] is False for row in rows))

    def test_bulk_delete_recomputes_customer_type(self):
        keep = self._order(status=OrderStatus.IN_DELIVERY)
        delivered = self._delivered()
        self.assertFalse(self._is_new())
        response = self.client.post(
            "/api/orders/bulk-delete", {"ids": [delivered.id]}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(self._is_new())
        # Đơn còn lại vẫn active.
        keep.refresh_from_db()
        self.assertIsNone(keep.deleted_at)


class ReconcileStatementTimeoutGuardTests(TestCase):
    """reconcile_customer_type phải chạy dưới guard statement_timeout như 4 bước
    precompute anh em, không rơi xuống trần toàn cục 30s (sự cố 2026-07-22)."""

    def test_reconcile_runs_under_report_query_timeout_guard(self):
        from unittest import mock
        from django.test import override_settings

        with override_settings(REPORT_QUERY_TIMEOUT_SECONDS=77):
            with mock.patch(
                "apps.customers.customer_type.statement_timeout"
            ) as guard:
                reconcile_customer_type()

        guard.assert_called_once_with(77)

    def test_reconcile_still_fixes_drift_with_guard_applied(self):
        """Guard không được đổi hành vi: drift vẫn phải được sửa."""
        channel = Channel.objects.create(code="ct-guard", name="Guard", owner="Guard")
        customer = Customer.objects.create(full_name="Drift KH")
        Order.objects.create(
            channel=channel,
            customer=customer,
            name="Đơn drift",
            phone="0911000009",
            status=OrderStatus.NEW,
            order_source="landing",
        )
        # Ép drift: đơn đã giao thành công nhưng cờ vẫn là "khách mới".
        Order.objects.filter(customer=customer).update(status=OrderStatus.DELIVERED_SUCCESS)
        Customer.objects.filter(pk=customer.pk).update(is_new_customer=True)

        reconcile_customer_type()

        customer.refresh_from_db()
        self.assertFalse(customer.is_new_customer)
