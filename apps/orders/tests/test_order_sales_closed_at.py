"""Mốc "ngày chốt" của đơn: luật ghi, backfill, và sắp xếp trên các trang đơn.

Bốn lớp bài test theo đúng các tầng của tính năng (xem
docs/orders-sort-by-closed-date.md):

- ``SalesClosedAtWriteRuleTests`` — luật ghi ``Order.sales_closed_at`` khi trạng
  thái sale đi qua lằn ranh nhóm chốt.
- ``BackfillSalesClosedAtCommandTests`` — command dựng lại mốc từ journey event
  phải ra ĐÚNG kết quả mà luật ghi ở trên sinh ra.
- ``OrdersSortClauseTests`` — hợp đồng của helper sắp xếp dùng chung.
- ``OrdersSortApiTests`` — 4 giá trị ``sort`` trên các endpoint danh sách đơn,
  gồm cả vị trí của đơn chưa chốt (NULL) ở cả hai chiều.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.models import Order, OrderJourneyEvent
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.transport.views.order_views_impl._view_helpers import (
    ORDERS_SORT_VALUES,
    apply_orders_sort,
)
from apps.users.tests.helpers import make_admin


class SalesClosedAtWriteRuleTests(TestCase):
    """Chỉ lằn ranh NGOÀI -> TRONG nhóm chốt mới ghi mốc."""

    def setUp(self):
        self.admin = make_admin("admin-sales-closed-at")
        self.channel = Channel.objects.create(code="lp-closed-at", name="Landing Closed At")
        self.customer = Customer.objects.create(
            full_name="Khach Chot",
            phone_raw="0909333001",
            phone_normalized="0909333001",
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, order_source="landing"
        )

    def _set_status(self, code: str) -> None:
        OrderService.update_order_inline(
            self.order, {"crm": {"sales_status_code": code}}, self.admin
        )

    def _closed_at(self):
        self.order.refresh_from_db()
        return self.order.sales_closed_at

    def test_new_order_has_no_closed_at(self):
        self.assertIsNone(self._closed_at())

    def test_entering_closed_group_stamps_the_moment(self):
        self._set_status("no_answer_2")
        self.assertIsNone(self._closed_at())

        self._set_status("closed")
        stamped = self._closed_at()
        self.assertIsNotNone(stamped)
        self.assertLess(timezone.now() - stamped, timedelta(seconds=30))

    def test_first_assessment_saved_already_closed_stamps_too(self):
        """Sale lưu đánh giá LẦN ĐẦU thẳng vào trạng thái chốt, không có trạng thái trước."""
        self._set_status("closed")
        self.assertIsNotNone(self._closed_at())

    def test_moving_inside_closed_group_keeps_the_original_stamp(self):
        """Đã chốt -> Đã lên đơn KHÔNG được đẩy mốc sang lúc lên đơn."""
        self._set_status("closed")
        first = self._closed_at()

        self._set_status("created_order")
        self.assertEqual(self._closed_at(), first)

    def test_leaving_closed_group_keeps_the_stamp(self):
        self._set_status("closed")
        first = self._closed_at()

        self._set_status("not_buying")
        self.assertEqual(self._closed_at(), first)

    def test_reclosing_after_falling_out_stamps_again(self):
        self._set_status("closed")
        first = self._closed_at()
        self._set_status("not_buying")

        self._set_status("closed_express")
        second = self._closed_at()
        self.assertIsNotNone(second)
        self.assertGreater(second, first)

    def test_non_closing_status_never_stamps(self):
        for code in ("no_answer_1", "need_more_consulting", "spam", "cancelled"):
            with self.subTest(code=code):
                self._set_status(code)
                self.assertIsNone(self._closed_at())

    def test_duplicated_order_carrying_closed_status_gets_its_own_stamp(self):
        """Đơn nhân bản mang trạng thái chốt thì phải có mốc chốt của CHÍNH NÓ."""
        self._set_status("created_order")
        original = self._closed_at()

        clone = OrderServiceHelpers.duplicate_order(self.order, self.admin, emit_event=False)

        self.assertIsNotNone(clone.sales_closed_at)
        self.assertGreaterEqual(clone.sales_closed_at, original)

    def test_duplicated_order_without_closed_status_has_no_stamp(self):
        self._set_status("no_answer_1")

        clone = OrderServiceHelpers.duplicate_order(self.order, self.admin, emit_event=False)

        self.assertIsNone(clone.sales_closed_at)

    def test_stamp_is_not_writable_from_client(self):
        """PATCH tay lên field này phải bị bỏ qua (read_only ở serializer)."""
        client = APIClient()
        client.force_authenticate(user=self.admin)
        forged = (timezone.now() - timedelta(days=30)).isoformat()

        response = client.patch(
            f"/api/orders/{self.order.id}", {"sales_closed_at": forged}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._closed_at())


class BackfillSalesClosedAtCommandTests(TestCase):
    """Command phải dựng lại ĐÚNG kết quả của luật ghi, từ journey event."""

    def setUp(self):
        self.channel = Channel.objects.create(code="lp-backfill-closed", name="Backfill Closed")
        self.customer = Customer.objects.create(
            full_name="Khach Backfill",
            phone_raw="0909333002",
            phone_normalized="0909333002",
        )

    def _order(self) -> Order:
        return Order.objects.create(
            channel=self.channel, customer=self.customer, order_source="landing"
        )

    @staticmethod
    def _event(order, *, to_code, from_code=None, days_ago=0):
        event = OrderJourneyEvent.objects.create(
            order=order,
            event_type="sales_status_changed",
            from_code=from_code,
            to_code=to_code,
        )
        # created_at là auto_now_add nên phải ghi đè bằng update().
        stamp = timezone.now() - timedelta(days=days_ago)
        OrderJourneyEvent.objects.filter(pk=event.pk).update(created_at=stamp)
        return stamp

    @staticmethod
    def _run(*args) -> str:
        out = StringIO()
        call_command("backfill_order_sales_closed_at", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_without_writing(self):
        order = self._order()
        self._event(order, to_code="closed", from_code="no_answer_1", days_ago=3)

        output = self._run()

        order.refresh_from_db()
        self.assertIsNone(order.sales_closed_at)
        self.assertIn("DRY-RUN", output)

    def test_apply_uses_latest_entry_into_closed_group(self):
        """Đơn rơi ra rồi chốt lại: lấy lần chốt GẦN NHẤT, không phải lần đầu."""
        order = self._order()
        self._event(order, to_code="closed", from_code="no_answer_1", days_ago=10)
        self._event(order, to_code="not_buying", from_code="closed", days_ago=5)
        latest = self._event(order, to_code="closed", from_code="not_buying", days_ago=2)

        self._run("--apply")

        order.refresh_from_db()
        self.assertEqual(order.sales_closed_at, latest)

    def test_transitions_inside_closed_group_are_ignored(self):
        """Đã chốt -> Đã lên đơn không phải mốc chốt; mốc vẫn là lần vào nhóm."""
        order = self._order()
        entered = self._event(order, to_code="closed", from_code="no_answer_1", days_ago=6)
        self._event(order, to_code="created_order", from_code="closed", days_ago=1)

        self._run("--apply")

        order.refresh_from_db()
        self.assertEqual(order.sales_closed_at, entered)

    def test_order_without_closing_event_stays_null(self):
        order = self._order()
        self._event(order, to_code="no_answer_1", days_ago=2)

        self._run("--apply")

        order.refresh_from_db()
        self.assertIsNone(order.sales_closed_at)

    def test_rerun_is_idempotent_and_does_not_overwrite(self):
        order = self._order()
        self._event(order, to_code="closed", from_code="no_answer_1", days_ago=4)
        self._run("--apply")
        order.refresh_from_db()
        first = order.sales_closed_at

        # Thêm một mốc chốt mới hơn: chạy lại KHÔNG được đụng ô đã có giá trị.
        self._event(order, to_code="closed", from_code="not_buying", days_ago=1)
        self._run("--apply")

        order.refresh_from_db()
        self.assertEqual(order.sales_closed_at, first)

    def test_force_overwrites_existing_value(self):
        order = self._order()
        self._event(order, to_code="closed", from_code="no_answer_1", days_ago=4)
        self._run("--apply")
        newer = self._event(order, to_code="closed", from_code="not_buying", days_ago=1)

        self._run("--apply", "--force")

        order.refresh_from_db()
        self.assertEqual(order.sales_closed_at, newer)


class OrdersSortClauseTests(SimpleTestCase):
    """Hợp đồng của helper sắp xếp dùng chung cho 4 endpoint list."""

    def test_declares_exactly_six_values(self):
        self.assertEqual(
            set(ORDERS_SORT_VALUES),
            {"newest", "oldest", "closed_newest", "closed_oldest", "amount_desc", "amount_asc"},
        )

    def test_unknown_value_falls_back_to_given_default(self):
        queryset = Order.objects.all()
        fallback = apply_orders_sort(queryset, "khong-ton-tai", default="oldest")
        expected = apply_orders_sort(queryset, "oldest")
        self.assertEqual(list(fallback.query.order_by), list(expected.query.order_by))


class OrdersSortApiTests(TestCase):
    """4 tuỳ chọn sắp xếp trên các endpoint danh sách đơn."""

    # Ba trang trong phạm vi tính năng, kèm `order_source` mà endpoint đó khoá
    # cứng (trang KOC chỉ trả đơn koc, trang Landing loại đơn koc). Thêm/bớt trang
    # thì sửa Ở ĐÂY — mỗi endpoint tự parse `sort` riêng nên thiếu một trang là
    # tuỳ chọn im lặng không chạy ở đúng trang đó.
    LIST_ENDPOINTS = (
        ("/api/orders", "landing"),
        ("/api/orders/koc", "koc"),
        ("/api/orders/landing", "landing"),
    )

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-orders-sort")
        self.channel = Channel.objects.create(code="lp-orders-sort", name="Orders Sort")
        self.customer = Customer.objects.create(
            full_name="Khach Sort",
            phone_raw="0909333003",
            phone_normalized="0909333003",
        )

        # Ngày TẠO và ngày CHỐT cố ý ngược nhau, để một thứ tự đúng không thể là
        # kết quả tình cờ của thứ tự kia.
        self.closed_early = self._order("closed-early", created_days_ago=1, closed_days_ago=9)
        self.closed_late = self._order("closed-late", created_days_ago=9, closed_days_ago=1)
        self.never_closed = self._order("never-closed", created_days_ago=5, closed_days_ago=None)

    def _order(self, name: str, *, created_days_ago: int, closed_days_ago, order_source="landing"):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name=name,
            order_source=order_source,
            sales_closed_at=(
                timezone.now() - timedelta(days=closed_days_ago)
                if closed_days_ago is not None
                else None
            ),
        )
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=created_days_ago)
        )
        return order

    def _ids(self, endpoint: str, sort: str):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            endpoint, {"sort": sort, "limit": "100", "offset": "0"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return [item["id"] for item in response.json()["results"]]

    def test_created_at_sort_still_works_both_ways(self):
        ids = self._ids("/api/orders", "newest")
        self.assertEqual(ids, [self.closed_early.id, self.never_closed.id, self.closed_late.id])
        self.assertEqual(self._ids("/api/orders", "oldest"), list(reversed(ids)))

    def test_closed_newest_puts_recently_closed_first(self):
        self.assertEqual(
            self._ids("/api/orders", "closed_newest"),
            [self.closed_late.id, self.closed_early.id, self.never_closed.id],
        )

    def test_closed_oldest_reverses_only_the_closed_orders(self):
        self.assertEqual(
            self._ids("/api/orders", "closed_oldest"),
            [self.closed_early.id, self.closed_late.id, self.never_closed.id],
        )

    def test_orders_without_closed_at_stay_last_in_both_directions(self):
        for sort in ("closed_newest", "closed_oldest"):
            with self.subTest(sort=sort):
                self.assertEqual(self._ids("/api/orders", sort)[-1], self.never_closed.id)

    def test_every_list_page_in_scope_accepts_the_new_sort(self):
        for endpoint, order_source in self.LIST_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                early = self._order(
                    f"{order_source}-early",
                    created_days_ago=1,
                    closed_days_ago=9,
                    order_source=order_source,
                )
                late = self._order(
                    f"{order_source}-late",
                    created_days_ago=9,
                    closed_days_ago=1,
                    order_source=order_source,
                )

                ids = self._ids(endpoint, "closed_newest")
                self.assertEqual([i for i in ids if i in (late.id, early.id)], [late.id, early.id])

    def test_unknown_sort_value_falls_back_instead_of_erroring(self):
        self.assertEqual(
            self._ids("/api/orders", "closed_sideways"), self._ids("/api/orders", "newest")
        )

    def test_list_payload_exposes_sales_closed_at(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders", {"limit": "100", "offset": "0"}, format="json"
        )
        rows = {item["id"]: item for item in response.json()["results"]}
        self.assertIsNotNone(rows[self.closed_late.id]["sales_closed_at"])
        self.assertIsNone(rows[self.never_closed.id]["sales_closed_at"])
