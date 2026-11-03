"""Test ContactListService — aggregate 1 dòng/khách cho trang Danh sách contact.

Xem docs/sale-contacts-page-design.md §3 và .superpowers/sdd/task-4-brief.md.

Gộp 2026-08-05 từ test_contact_list_service.py + test_contact_list_operation.py +
test_contact_list_api.py (xem docs/test-restructure-2026-08-05.md).
"""
import pytest
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.contact_flow.models import CustomerOperationState, OperationStage
from apps.customers.models import ContactActivity, Customer
from apps.orders.application.services.contact_list_service import ContactListService
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderJourneyEvent, OrderSource, OrderStatus
from apps.catalog.models import Product
from apps.orders.transport.views.order_views_impl.contact_list_view import (
    _parse_limit,
    _parse_offset,
)
from apps.users.models import DataScope, Permission, Role, RolePermission, RoleSectionScope, User, UserRoleAssignment
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_admin, make_user_with_sections


def _customer(**kwargs):
    defaults = dict(full_name="KH Test", phone_raw="0900000000", phone_normalized="0900000000")
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def _own_scope_user(username: str) -> User:
    """User có contacts:view/edit nhưng scope section contacts = OWN."""
    sync_permissions()
    user = User.objects.create_user(username=username, password="x")
    role = Role.objects.create(name=f"role-{username}", scope_default=DataScope.ALL)
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission=p)
        for p in Permission.objects.filter(section="contacts")
    )
    RoleSectionScope.objects.create(role=role, section="contacts", scope=DataScope.OWN)
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


class ContactListServiceTestsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="contacts-ch", name="Contacts Channel")

    def _order(self, customer, *, staff=None, created_at=None, order_source=OrderSource.LANDING, status=OrderStatus.NEW, sales_status_code=None):
        # Cờ Customer.is_new_customer tính lại qua event orders.saved (publish_on_commit)
        # — trong TestCase phải chạy callback on_commit tay, không thì cờ không đổi.
        with self.captureOnCommitCallbacks(execute=True):
            order = Order.objects.create(
                channel=self.channel,
                customer=customer,
                staff=staff,
                order_source=order_source,
                status=status,
            )
        if created_at is not None:
            Order.objects.filter(pk=order.pk).update(created_at=created_at)
            order.refresh_from_db()
        if sales_status_code is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status_code)
        return order


class OneRowPerCustomerLatestOrderTests(ContactListServiceTestsBase):
    def setUp(self):
        self.admin = make_admin("admin-contacts-1")
        now = timezone.now()
        self.customer = _customer(full_name="Nguyen Van A", phone_raw="0911111111", phone_normalized="0911111111")
        self.order_old = self._order(self.customer, created_at=now - timedelta(days=2))
        self.order_new = self._order(self.customer, created_at=now - timedelta(days=1))

    def test_one_row_per_customer_latest_order(self):
        result = ContactListService.list_contacts(user=self.admin)
        items = [i for i in result["items"] if i["customer_id"] == self.customer.id]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["latest_order"]["id"], self.order_new.id)


class MarketplaceAndKocExcludedTests(ContactListServiceTestsBase):
    def setUp(self):
        self.admin = make_admin("admin-contacts-2")
        self.landing_customer = _customer(full_name="Landing KH", phone_raw="0911111112", phone_normalized="0911111112")
        self.shopee_customer = _customer(full_name="Shopee KH", phone_raw="0911111113", phone_normalized="0911111113")
        self.koc_customer = _customer(full_name="Koc KH", phone_raw="0911111114", phone_normalized="0911111114")
        self.landing_order = self._order(self.landing_customer, order_source=OrderSource.LANDING)
        self.shopee_order = self._order(self.shopee_customer, order_source=OrderSource.SHOPEE)
        self.koc_order = self._order(self.koc_customer, order_source=OrderSource.KOC)

    def test_marketplace_and_koc_excluded(self):
        result = ContactListService.list_contacts(user=self.admin)
        customer_ids = {i["customer_id"] for i in result["items"]}
        self.assertIn(self.landing_customer.id, customer_ids)
        self.assertNotIn(self.shopee_customer.id, customer_ids)
        self.assertNotIn(self.koc_customer.id, customer_ids)


class ActionCountsPendingVsTotalTests(ContactListServiceTestsBase):
    def setUp(self):
        self.admin = make_admin("admin-contacts-3")
        self.customer = _customer(full_name="Chua Tuong Tac", phone_raw="0911111115", phone_normalized="0911111115", is_new_customer=True)
        self.order = self._order(self.customer, sales_status_code=None)

    def test_no_activity_counts_as_pending(self):
        result = ContactListService.list_contacts(user=self.admin)
        counts = result["action_counts"]["new_customer"]
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["pending"], 1)

    def test_activity_after_order_reduces_pending(self):
        ContactActivity.objects.create(customer=self.customer, kind="note", note="da goi")
        result = ContactListService.list_contacts(user=self.admin)
        counts = result["action_counts"]["new_customer"]
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["pending"], 0)


class ScopeOwnFiltersByStaffTests(ContactListServiceTestsBase):
    def setUp(self):
        self.owner = _own_scope_user("sale-own")
        self.other = User.objects.create_user(username="sale-other", password="x")

        self.mine_customer = _customer(full_name="Cua Toi", phone_raw="0911111116", phone_normalized="0911111116")
        self.other_customer = _customer(full_name="Cua Nguoi Khac", phone_raw="0911111117", phone_normalized="0911111117")
        self.mine_order = self._order(self.mine_customer, staff=self.owner)
        self.other_order = self._order(self.other_customer, staff=self.other)

    def test_scope_own_filters_by_staff(self):
        result = ContactListService.list_contacts(user=self.owner)
        customer_ids = {i["customer_id"] for i in result["items"]}
        self.assertIn(self.mine_customer.id, customer_ids)
        self.assertNotIn(self.other_customer.id, customer_ids)


class FilterByActionNeededAndInteractionTests(ContactListServiceTestsBase):
    """"new_lead" (thay vì "new_customer") đòi Customer.is_new_customer=False, một cờ
    TÍNH LẠI bởi subscriber customer_type (>1 đơn giao thành công) — không set tay được.
    Dựng 2 đơn giao thành công cũ trước, rồi đơn mới nhất (chưa có sales_status)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-4")
        now = timezone.now()
        self.new_lead_customer = _customer(full_name="New Lead", phone_raw="0911111118", phone_normalized="0911111118")
        self._order(
            self.new_lead_customer, created_at=now - timedelta(days=10), status=OrderStatus.DELIVERED_SUCCESS
        )
        self._order(
            self.new_lead_customer, created_at=now - timedelta(days=9), status=OrderStatus.DELIVERED_SUCCESS
        )
        self.new_lead_order = self._order(self.new_lead_customer, created_at=now)
        self.new_lead_customer.refresh_from_db()
        assert self.new_lead_customer.is_new_customer is False

        self.recent_customer = _customer(
            full_name="Recent Interaction", phone_raw="0911111119", phone_normalized="0911111119"
        )
        self.recent_order = self._order(self.recent_customer, created_at=now, sales_status_code="no_answer_1")
        ContactActivity.objects.create(customer=self.recent_customer, kind="call", note="ok")

    def test_filter_by_action_needed(self):
        result = ContactListService.list_contacts(user=self.admin, action_needed=["new_lead"])
        customer_ids = {i["customer_id"] for i in result["items"]}
        self.assertIn(self.new_lead_customer.id, customer_ids)
        self.assertNotIn(self.recent_customer.id, customer_ids)

    def test_filter_by_interaction_none(self):
        result = ContactListService.list_contacts(user=self.admin, interaction="none")
        customer_ids = {i["customer_id"] for i in result["items"]}
        self.assertIn(self.new_lead_customer.id, customer_ids)
        self.assertNotIn(self.recent_customer.id, customer_ids)

    def test_filter_by_interaction_recent(self):
        result = ContactListService.list_contacts(user=self.admin, interaction="recent")
        customer_ids = {i["customer_id"] for i in result["items"]}
        self.assertNotIn(self.new_lead_customer.id, customer_ids)
        self.assertIn(self.recent_customer.id, customer_ids)


class PaginationTotalTests(ContactListServiceTestsBase):
    def setUp(self):
        self.admin = make_admin("admin-contacts-5")
        for idx in range(5):
            customer = _customer(
                full_name=f"KH Page {idx}", phone_raw=f"09122220{idx}", phone_normalized=f"09122220{idx}"
            )
            self._order(customer)

    def test_pagination_total(self):
        result = ContactListService.list_contacts(user=self.admin, limit=2, offset=0)
        self.assertEqual(result["total"], 5)
        self.assertEqual(len(result["items"]), 2)

        result_page2 = ContactListService.list_contacts(user=self.admin, limit=2, offset=2)
        self.assertEqual(len(result_page2["items"]), 2)
        self.assertEqual(result_page2["total"], 5)

        first_page_ids = {i["customer_id"] for i in result["items"]}
        second_page_ids = {i["customer_id"] for i in result_page2["items"]}
        self.assertFalse(first_page_ids & second_page_ids)


class SortOrderTests(ContactListServiceTestsBase):
    """Danh sách contact sắp theo cột "Thời gian chờ" (waiting_since) TĂNG DẦN
    (hotfix 2026-09-15): khách chờ lâu nhất đứng đầu. Dòng không có tác nghiệp
    (ô chờ trống) dồn cuối."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-sort")
        self.now = timezone.now()
        self.c_oldest = _customer(full_name="KH Som", phone_raw="0912999001", phone_normalized="0912999001")
        self.c_middle = _customer(full_name="KH Giua", phone_raw="0912999002", phone_normalized="0912999002")
        self.c_newest = _customer(full_name="KH Muon", phone_raw="0912999003", phone_normalized="0912999003")
        # Tạo theo thứ tự đảo để không dựa vào id tăng dần.
        self._order(self.c_newest, created_at=self.now - timedelta(hours=1))
        self._order(self.c_oldest, created_at=self.now - timedelta(days=3))
        self._order(self.c_middle, created_at=self.now - timedelta(days=1))

    def _ids(self, **kwargs):
        return [i["customer_id"] for i in ContactListService.list_contacts(user=self.admin, **kwargs)["items"]]

    def test_items_sorted_by_waiting_since_ascending(self):
        # Chưa tương tác → mốc chờ = ngày data về.
        self.assertEqual(self._ids(), [self.c_oldest.id, self.c_middle.id, self.c_newest.id])

    def test_recent_activity_pushes_customer_down(self):
        """Khách cũ nhất vừa được gọi → mốc chờ nhảy lên hiện tại → xuống cuối,
        dù ngày data về vẫn sớm nhất."""
        activity = ContactActivity.objects.create(
            customer=self.c_oldest, kind="call", note="đã gọi"
        )
        ContactActivity.objects.filter(pk=activity.pk).update(created_at=self.now - timedelta(minutes=5))

        self.assertEqual(self._ids(), [self.c_middle.id, self.c_newest.id, self.c_oldest.id])

    def test_no_action_rows_go_last(self):
        c_cancelled = _customer(full_name="KH Huy", phone_raw="0912999004", phone_normalized="0912999004")
        self._order(c_cancelled, created_at=self.now - timedelta(days=10), status=OrderStatus.CANCELLED)

        self.assertEqual(self._ids()[-1], c_cancelled.id)

    def test_pagination_keeps_order_across_pages(self):
        page1 = ContactListService.list_contacts(user=self.admin, limit=2, offset=0)
        page2 = ContactListService.list_contacts(user=self.admin, limit=2, offset=2)
        self.assertEqual([i["customer_id"] for i in page1["items"]], [self.c_oldest.id, self.c_middle.id])
        self.assertEqual([i["customer_id"] for i in page2["items"]], [self.c_newest.id])


class SourceFilterUnionTests(ContactListServiceTestsBase):
    """Bộ lọc "Nguồn" trên UI gộp kênh (channel) + nguồn đơn (order_source) vào MỘT
    multi-select → khi gửi cả hai, BE phải lấy HỢP (OR) chứ không phải GIAO (AND):
    chọn "Kênh X" + "Zalo" nghĩa là khách thuộc kênh X HOẶC đơn từ Zalo."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_channel = Channel.objects.create(code="contacts-ch-2", name="Contacts Channel 2")

    def setUp(self):
        self.admin = make_admin("admin-contacts-source-union")
        self.c_channel = _customer(full_name="KH Kenh", phone_raw="0912888001", phone_normalized="0912888001")
        self.c_source = _customer(full_name="KH Nguon", phone_raw="0912888002", phone_normalized="0912888002")
        self.c_neither = _customer(full_name="KH Khac", phone_raw="0912888003", phone_normalized="0912888003")
        # Khớp kênh (channel mặc định), nguồn landing.
        self._order(self.c_channel, order_source=OrderSource.LANDING)
        # Khớp nguồn zalo nhưng kênh khác.
        Order.objects.create(
            channel=self.other_channel, customer=self.c_source, order_source=OrderSource.ZALO, status=OrderStatus.NEW
        )
        # Không khớp gì: kênh khác + landing.
        Order.objects.create(
            channel=self.other_channel, customer=self.c_neither, order_source=OrderSource.LANDING, status=OrderStatus.NEW
        )

    def test_channel_and_order_source_are_unioned(self):
        result = ContactListService.list_contacts(
            user=self.admin, channel_ids=[self.channel.id], order_sources=[OrderSource.ZALO]
        )
        self.assertEqual(
            sorted(i["customer_id"] for i in result["items"]),
            sorted([self.c_channel.id, self.c_source.id]),
        )

    def test_channel_only_still_narrows(self):
        result = ContactListService.list_contacts(user=self.admin, channel_ids=[self.channel.id])
        self.assertEqual([i["customer_id"] for i in result["items"]], [self.c_channel.id])

    def test_order_source_only_still_narrows(self):
        result = ContactListService.list_contacts(user=self.admin, order_sources=[OrderSource.ZALO])
        self.assertEqual([i["customer_id"] for i in result["items"]], [self.c_source.id])


class LatestOrderAddressTests(ContactListServiceTestsBase):
    """Cột "Địa chỉ" = địa chỉ chuẩn hoá của đơn mới nhất; đơn chưa chuẩn hoá rơi về
    chuỗi thô khách nhập; không có gì thì rỗng (không None)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-address")

    def _item(self, customer):
        result = ContactListService.list_contacts(user=self.admin)
        return next(i for i in result["items"] if i["customer_id"] == customer.id)

    def test_uses_normalized_address(self):
        customer = _customer(full_name="KH Dia Chi", phone_raw="0912777001", phone_normalized="0912777001")
        order = self._order(customer)
        Order.objects.filter(pk=order.pk).update(
            address="12 Lê Lợi, Phường Lạch Tray, Quận Ngô Quyền, Thành phố Hải Phòng",
            raw_address="12 le loi ngo quyen hp",
        )
        self.assertEqual(
            self._item(customer)["latest_order"]["address"],
            "12 Lê Lợi, Phường Lạch Tray, Quận Ngô Quyền, Thành phố Hải Phòng",
        )

    def test_falls_back_to_raw_address(self):
        customer = _customer(full_name="KH Tho", phone_raw="0912777002", phone_normalized="0912777002")
        order = self._order(customer)
        Order.objects.filter(pk=order.pk).update(address="", raw_address="12 le loi ngo quyen hp")
        self.assertEqual(self._item(customer)["latest_order"]["address"], "12 le loi ngo quyen hp")

    def test_blank_when_order_has_no_address(self):
        customer = _customer(full_name="KH Trong", phone_raw="0912777003", phone_normalized="0912777003")
        self._order(customer)
        self.assertEqual(self._item(customer)["latest_order"]["address"], "")


class ActionCountsRespectScopeFiltersTests(ContactListServiceTestsBase):
    """action_counts phải tính trên tập đã áp interaction+customer_type (design §5:
    y = "tổng contact thuộc nhóm TRONG BỘ LỌC"), nhưng KHÔNG áp action_needed (chip
    phải giữ nguyên số để còn bấm-chuyển-chip được)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-scope")
        now = timezone.now()
        # 2 khách mới, chưa tương tác -> action_needed = new_customer, pending.
        self.new_customer_1 = _customer(
            full_name="New 1", phone_raw="0911111201", phone_normalized="0911111201", is_new_customer=True
        )
        self._order(self.new_customer_1, created_at=now)
        self.new_customer_2 = _customer(
            full_name="New 2", phone_raw="0911111202", phone_normalized="0911111202", is_new_customer=True
        )
        self._order(self.new_customer_2, created_at=now)
        # 1 khách cũ (>=1 đơn giao thành công trước đó -> subscriber set is_new_customer=False),
        # chưa tương tác trên đơn mới nhất -> action_needed = new_lead, pending.
        self.old_customer = _customer(
            full_name="Old 1", phone_raw="0911111203", phone_normalized="0911111203"
        )
        self._order(self.old_customer, created_at=now - timedelta(days=10), status=OrderStatus.DELIVERED_SUCCESS)
        self._order(self.old_customer, created_at=now)
        self.old_customer.refresh_from_db()
        assert self.old_customer.is_new_customer is False

    def test_no_filter_matches_today_behavior(self):
        result = ContactListService.list_contacts(user=self.admin)
        self.assertEqual(result["action_counts"]["all"]["total"], 3)
        self.assertEqual(result["action_counts"]["new_customer"]["total"], 2)
        self.assertEqual(result["action_counts"]["new_lead"]["total"], 1)
        self.assertEqual(result["total"], 3)

    def test_customer_type_filter_shrinks_action_counts(self):
        result = ContactListService.list_contacts(user=self.admin, customer_type="new")
        # 2 khách mới nằm trong bộ lọc -> "all" phải = 2, KHÔNG phải 3 (tổng chưa lọc).
        self.assertEqual(result["action_counts"]["all"]["total"], 2)
        self.assertEqual(result["action_counts"]["new_customer"]["total"], 2)
        self.assertNotIn("new_lead", result["action_counts"])
        self.assertEqual(result["total"], 2)

    def test_interaction_filter_shrinks_action_counts(self):
        # Khách cũ có hoạt động gần đây -> không còn "chưa tương tác".
        ContactActivity.objects.create(customer=self.old_customer, kind="note", note="da lien he")
        result = ContactListService.list_contacts(user=self.admin, interaction="none")
        self.assertEqual(result["action_counts"]["all"]["total"], 2)
        self.assertEqual(result["action_counts"]["new_customer"]["total"], 2)
        self.assertNotIn("new_lead", result["action_counts"])
        self.assertEqual(result["total"], 2)

    def test_action_needed_filter_does_not_change_action_counts(self):
        baseline = ContactListService.list_contacts(user=self.admin)
        clicked = ContactListService.list_contacts(user=self.admin, action_needed=["new_customer"])
        self.assertEqual(clicked["action_counts"], baseline["action_counts"])
        # Nhưng total phân trang phải chỉ còn khách trong chip đã bấm.
        self.assertEqual(clicked["total"], 2)
        self.assertEqual(baseline["total"], 3)


class OrderJourneyEventActivityTests(ContactListServiceTestsBase):
    """Sales-status change (OrderJourneyEvent) cũng tính là đã tương tác."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-6")
        self.customer = _customer(full_name="Journey KH", phone_raw="0911111199", phone_normalized="0911111199")
        self.order = self._order(self.customer, sales_status_code="no_answer_1")
        OrderJourneyEvent.objects.create(order=self.order, event_type="sales_status_changed")

    def test_journey_event_counts_as_activity(self):
        result = ContactListService.list_contacts(user=self.admin)
        item = next(i for i in result["items"] if i["customer_id"] == self.customer.id)
        self.assertFalse(item["waiting_since"] is None and item["last_activity_at"] is None)
        self.assertIsNotNone(item["last_activity_at"])


class EmptyResultActionCountsTests(ContactListServiceTestsBase):
    """Hợp đồng: action_counts LUÔN có key "all" — kể cả khi kết quả rỗng."""

    def setUp(self):
        # Có data trong hệ thống nhưng user OWN không phụ trách đơn nào → tập rỗng.
        self.own_user = _own_scope_user("sale-own-empty")
        other = User.objects.create_user(username="sale-other-empty", password="x")
        customer = _customer(full_name="KH Nguoi Khac", phone_raw="0911111120", phone_normalized="0911111120")
        self._order(customer, staff=other)

    def test_empty_result_still_has_all_key(self):
        result = ContactListService.list_contacts(user=self.own_user)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["action_counts"]["all"], {"pending": 0, "total": 0})


class OrderCountScopeTests(ContactListServiceTestsBase):
    """order_count phải áp cùng RBAC scope: user OWN chỉ đếm đơn mình phụ trách."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-7")
        self.own_user = _own_scope_user("sale-own-count")
        self.other = User.objects.create_user(username="sale-other-count", password="x")
        now = timezone.now()
        self.customer = _customer(full_name="KH Hai Sale", phone_raw="0911111121", phone_normalized="0911111121")
        self._order(self.customer, staff=self.other, created_at=now - timedelta(days=1))
        self._order(self.customer, staff=self.own_user, created_at=now)

    def _item(self, user):
        result = ContactListService.list_contacts(user=user)
        return next(i for i in result["items"] if i["customer_id"] == self.customer.id)

    def test_own_scope_counts_only_own_orders(self):
        self.assertEqual(self._item(self.own_user)["order_count"], 1)

    def test_all_scope_counts_all_orders(self):
        self.assertEqual(self._item(self.admin)["order_count"], 2)


class DateRangeBoundaryTests(ContactListServiceTestsBase):
    """Lọc ngày dùng khoảng timestamp [00:00 from, 00:00 to+1) theo giờ địa phương —
    ngữ nghĩa phải KHỚP hành vi created_at__date cũ ở biên (không dùng cast ::date)."""

    def setUp(self):
        from datetime import date, datetime, time

        self.admin = make_admin("admin-contacts-date")
        self.d = date(2026, 7, 10)

        def _local(dt_date, t):
            return timezone.make_aware(datetime.combine(dt_date, t))

        # 4 khách, mỗi khách 1 đơn tại một mốc biên khác nhau.
        self.c_start = _customer(full_name="Start", phone_raw="0921000001", phone_normalized="0921000001")
        self.c_end = _customer(full_name="End", phone_raw="0921000002", phone_normalized="0921000002")
        self.c_before = _customer(full_name="Before", phone_raw="0921000003", phone_normalized="0921000003")
        self.c_after = _customer(full_name="After", phone_raw="0921000004", phone_normalized="0921000004")

        # 00:00 ngày from → PHẢI nằm trong (>= start)
        self._order(self.c_start, created_at=_local(self.d, time(0, 0, 0)))
        # 23:59:59 ngày to → PHẢI nằm trong (< 00:00 ngày to+1)
        self._order(self.c_end, created_at=_local(self.d, time(23, 59, 59)))
        # 23:59:59 ngày trước from → PHẢI bị loại
        self._order(self.c_before, created_at=_local(date(2026, 7, 9), time(23, 59, 59)))
        # 00:00 ngày to+1 → PHẢI bị loại
        self._order(self.c_after, created_at=_local(date(2026, 7, 11), time(0, 0, 0)))

    def test_boundary_inclusion_matches_local_day_window(self):
        result = ContactListService.list_contacts(
            user=self.admin, date_from=self.d, date_to=self.d
        )
        ids = {i["customer_id"] for i in result["items"]}
        self.assertIn(self.c_start.id, ids)
        self.assertIn(self.c_end.id, ids)
        self.assertNotIn(self.c_before.id, ids)
        self.assertNotIn(self.c_after.id, ids)


# ══════════════════════════════════════════════════════════════════════════════
# Test ContactListService — block `operation` (state tác nghiệp CSKH) + bucket filter
# + operation_counts trên GET /api/sale/contacts (Task S2-2).
#
# Mirror fixture base từ ContactListServiceTestsBase ở trên — không viết lại
# _order()/_customer(). (Gộp từ test_contact_list_operation.py.)
# ══════════════════════════════════════════════════════════════════════════════


class OperationBlockInItemsTests(ContactListServiceTestsBase):
    """4 khách: overdue / today / ended (state nhưng due_at null) / none (không có state)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-op-1")
        self.stage = OperationStage.objects.create(
            name="Gọi lần đầu", color="#ff0000", allow_edit_due=True
        )
        now = timezone.now()
        # due_at của khách "today" phải nằm TRONG ngày local và vẫn ở tương lai:
        # bucket_of trả "today" chỉ khi due_at >= now VÀ cùng ngày local (xem
        # contact_flow/.../runtime_serializers.py). Trước đây fixture dùng cứng
        # now + 30 phút — chạy suite sau 23h30 thì due_at rơi sang hôm sau và test đỏ
        # theo GIỜ CHẠY chứ không theo code (bắt được 2026-07-26 lúc 23:5x).
        # Lấy trung điểm giữa now và cuối ngày để luôn thoả cả hai điều kiện.
        local_now = timezone.localtime(now)
        end_of_local_day = local_now.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        due_today = local_now + (end_of_local_day - local_now) / 2

        self.customer_overdue = _customer(
            full_name="Overdue", phone_raw="0931000001", phone_normalized="0931000001"
        )
        self._order(self.customer_overdue, created_at=now)
        CustomerOperationState.objects.create(
            customer=self.customer_overdue,
            current_stage=self.stage,
            due_at=now - timedelta(hours=2),
            entered_at=now,
        )

        self.customer_today = _customer(
            full_name="Today", phone_raw="0931000002", phone_normalized="0931000002"
        )
        self._order(self.customer_today, created_at=now)
        CustomerOperationState.objects.create(
            customer=self.customer_today,
            current_stage=self.stage,
            due_at=due_today,
            entered_at=now,
        )

        self.customer_ended = _customer(
            full_name="Ended", phone_raw="0931000003", phone_normalized="0931000003"
        )
        self._order(self.customer_ended, created_at=now)
        CustomerOperationState.objects.create(
            customer=self.customer_ended,
            current_stage=self.stage,
            due_at=None,
            entered_at=now,
        )

        self.customer_none = _customer(
            full_name="No State", phone_raw="0931000004", phone_normalized="0931000004"
        )
        self._order(self.customer_none, created_at=now)
        # Không tạo CustomerOperationState cho khách này.

    def _item(self, result, customer_id):
        return next(i for i in result["items"] if i["customer_id"] == customer_id)

    def test_bucket_overdue(self):
        result = ContactListService.list_contacts(user=self.admin)
        item = self._item(result, self.customer_overdue.id)
        self.assertEqual(item["operation"]["bucket"], "overdue")
        self.assertEqual(item["operation"]["stage_name"], "Gọi lần đầu")
        self.assertEqual(item["operation"]["stage_color"], "#ff0000")
        self.assertTrue(item["operation"]["allow_edit_due"])
        self.assertIsNotNone(item["operation"]["due_at"])

    def test_bucket_today(self):
        result = ContactListService.list_contacts(user=self.admin)
        item = self._item(result, self.customer_today.id)
        self.assertEqual(item["operation"]["bucket"], "today")

    def test_bucket_ended_when_due_at_null(self):
        result = ContactListService.list_contacts(user=self.admin)
        item = self._item(result, self.customer_ended.id)
        self.assertEqual(item["operation"]["bucket"], "ended")

    def test_bucket_none_when_no_state(self):
        result = ContactListService.list_contacts(user=self.admin)
        item = self._item(result, self.customer_none.id)
        self.assertEqual(
            item["operation"],
            {
                "stage_name": "",
                "stage_color": "",
                "allow_edit_due": False,
                "due_at": None,
                "bucket": "none",
            },
        )

    def test_operation_bucket_filter_returns_only_matching(self):
        result = ContactListService.list_contacts(user=self.admin, operation_bucket="overdue")
        customer_ids = {i["customer_id"] for i in result["items"]}
        self.assertEqual(customer_ids, {self.customer_overdue.id})
        self.assertEqual(result["total"], 1)


class OperationCountsFullSetTests(ContactListServiceTestsBase):
    """operation_counts phải đếm trên TOÀN BỘ kết quả sau scope filter, KHÔNG chỉ trang
    hiện tại — dùng limit nhỏ để phân biệt total vs items[]."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-op-2")
        self.stage = OperationStage.objects.create(name="Bước 1")
        now = timezone.now()

        self.overdue_customers = []
        for idx in range(3):
            customer = _customer(
                full_name=f"Overdue {idx}",
                phone_raw=f"09320000{idx}",
                phone_normalized=f"09320000{idx}",
            )
            self._order(customer, created_at=now)
            CustomerOperationState.objects.create(
                customer=customer,
                current_stage=self.stage,
                due_at=now - timedelta(hours=1),
                entered_at=now,
            )
            self.overdue_customers.append(customer)

        self.none_customer = _customer(
            full_name="No State Counts", phone_raw="0932000009", phone_normalized="0932000009"
        )
        self._order(self.none_customer, created_at=now)

    def test_operation_counts_count_all_filtered_rows_not_just_page(self):
        result = ContactListService.list_contacts(user=self.admin, limit=1, offset=0)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["operation_counts"]["overdue"]["total"], 3)
        self.assertEqual(result["operation_counts"]["none"]["total"], 1)
        self.assertEqual(result["operation_counts"]["all"]["total"], 4)

    def test_operation_counts_present_even_when_operation_bucket_filter_applied(self):
        """operation_counts đếm TRƯỚC khi áp operation_bucket filter — bấm 1 bucket
        không được làm co số các bucket còn lại (mirror hành vi action_counts)."""
        result = ContactListService.list_contacts(user=self.admin, operation_bucket="overdue")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["operation_counts"]["overdue"]["total"], 3)
        self.assertEqual(result["operation_counts"]["none"]["total"], 1)
        self.assertEqual(result["operation_counts"]["all"]["total"], 4)

    def test_no_matching_bucket_filter_empties_items_but_keeps_counts(self):
        """Không có khách bucket="today" -> items rỗng, nhưng operation_counts["all"]
        vẫn phải giữ nguyên tổng của TOÀN BỘ tập đã scope (không co theo filter đã bấm),
        mirror hành vi action_needed filter (test_action_needed_filter_does_not_change_action_counts)."""
        result = ContactListService.list_contacts(user=self.admin, operation_bucket="today")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["operation_counts"]["all"]["total"], 4)


class TrulyEmptyResultOperationCountsTests(ContactListServiceTestsBase):
    """Hợp đồng: operation_counts LUÔN có key "all" kể cả khi rows rỗng ngay từ đầu
    (không phải do operation_bucket filter co lại) — nhánh early-return."""

    def setUp(self):
        self.own_user = _own_scope_user("sale-own-op-empty")
        other = User.objects.create_user(username="sale-other-op-empty", password="x")
        customer = _customer(
            full_name="KH Nguoi Khac Op", phone_raw="0934000001", phone_normalized="0934000001"
        )
        self._order(customer, staff=other)

    def test_early_return_still_has_operation_counts_all_key(self):
        result = ContactListService.list_contacts(user=self.own_user)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["operation_counts"]["all"], {"pending": 0, "total": 0})


class OperationStateQueryCountTests(ContactListServiceTestsBase):
    """Bulk-fetch CustomerOperationState phải là ĐÚNG 1 query thêm — không N+1 theo số
    khách. So sánh query count giữa 1 khách và nhiều khách: phải bằng nhau."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-op-3")
        self.stage = OperationStage.objects.create(name="Bước 1")
        now = timezone.now()
        self.customers = []
        for idx in range(4):
            customer = _customer(
                full_name=f"Query {idx}", phone_raw=f"09330000{idx}", phone_normalized=f"09330000{idx}"
            )
            self._order(customer, created_at=now)
            CustomerOperationState.objects.create(
                customer=customer, current_stage=self.stage, due_at=now, entered_at=now
            )
            self.customers.append(customer)

    def test_query_count_does_not_grow_with_customer_count(self):
        one_customer = _customer(
            full_name="Solo", phone_raw="0933999999", phone_normalized="0933999999"
        )
        self._order(one_customer, created_at=timezone.now())
        CustomerOperationState.objects.create(
            customer=one_customer, current_stage=self.stage, due_at=timezone.now(), entered_at=timezone.now()
        )

        # Mồi RBAC cache trước (query rbac_roles chỉ chạy ở lần gọi đầu, gây lệch số
        # query giữa 2 lần đo nếu không mồi trước — không liên quan tới thay đổi này).
        ContactListService.list_contacts(user=self.admin, q=one_customer.phone_raw)

        with CaptureQueriesContext(connection) as solo_ctx:
            ContactListService.list_contacts(
                user=self.admin, q=one_customer.phone_raw
            )
        solo_count = len(solo_ctx.captured_queries)

        with CaptureQueriesContext(connection) as many_ctx:
            ContactListService.list_contacts(user=self.admin)
        many_count = len(many_ctx.captured_queries)

        # Cùng shape truy vấn (bulk IN, không phải 1 query/khách) → số query bằng nhau
        # dù số khách trong tập kết quả khác nhau.
        self.assertEqual(solo_count, many_count)


# ══════════════════════════════════════════════════════════════════════════════
# API test GET /api/sale/contacts — xem .superpowers/sdd/task-5-brief.md.
# (Gộp từ test_contact_list_api.py.)
# ══════════════════════════════════════════════════════════════════════════════


def _customer_api(**kwargs):
    defaults = dict(full_name="KH Contacts API", phone_raw="0922222222", phone_normalized="0922222222")
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


class ContactListApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="contacts-api-ch", name="Contacts API Channel")

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-contacts-api")
        now = timezone.now()
        self.customer_a = _customer_api(
            full_name="Nguyen Van A", phone_raw="0922222221", phone_normalized="0922222221"
        )
        self.customer_b = _customer_api(
            full_name="Tran Thi B", phone_raw="0922222222", phone_normalized="0922222222"
        )
        self.order_a = Order.objects.create(
            channel=self.channel, customer=self.customer_a, order_source="landing"
        )
        Order.objects.filter(pk=self.order_a.pk).update(created_at=now - timedelta(days=1))
        self.order_a.refresh_from_db()
        OrderAssessment.objects.create(order=self.order_a, sales_status_code="scheduled")

        self.order_b = Order.objects.create(
            channel=self.channel, customer=self.customer_b, order_source="landing"
        )
        Order.objects.filter(pk=self.order_b.pk).update(created_at=now - timedelta(days=10))
        self.order_b.refresh_from_db()

    def test_returns_200_with_items_total_action_counts(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/sale/contacts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)
        self.assertIn("total", data)
        self.assertIn("action_counts", data)
        self.assertIn("all", data["action_counts"])
        self.assertEqual(data["total"], 2)

        item = next(i for i in data["items"] if i["customer_id"] == self.customer_a.id)
        # ISO-8601 datetime strings, not raw python datetime repr.
        created_at = item["latest_order"]["created_at"]
        self.assertIsInstance(created_at, str)
        self.assertRegex(created_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_forbidden_without_permission(self):
        no_access_user = make_user_with_sections("no-access-contacts-api", [])
        self.client.force_authenticate(user=no_access_user)
        response = self.client.get("/api/sale/contacts")
        self.assertEqual(response.status_code, 403)

    def test_limit_over_max_still_succeeds(self):
        # Smoke: raw limit=500 must not 400/500 (the actual clamp value is
        # asserted directly in LimitOffsetParsingTests below).
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/sale/contacts", {"limit": "500"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 2)

    def test_date_filter_excludes_older_contact(self):
        self.client.force_authenticate(user=self.admin)
        date_from = (timezone.now() - timedelta(days=3)).date().isoformat()
        response = self.client.get("/api/sale/contacts", {"date_from": date_from})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        customer_ids = {i["customer_id"] for i in data["items"]}
        self.assertIn(self.customer_a.id, customer_ids)
        self.assertNotIn(self.customer_b.id, customer_ids)


class LimitOffsetParsingTests(SimpleTestCase):
    """Regression guard trực tiếp cho clamp limit/offset của view (không cần DB)."""

    def test_limit_over_max_clamped_to_100(self):
        self.assertEqual(_parse_limit("500"), 100)

    def test_limit_invalid_falls_back_to_default(self):
        self.assertEqual(_parse_limit("abc"), 20)

    def test_limit_zero_falls_back_to_default(self):
        self.assertEqual(_parse_limit("0"), 20)

    def test_limit_negative_falls_back_to_default(self):
        self.assertEqual(_parse_limit("-3"), 20)

    def test_limit_missing_uses_default(self):
        self.assertEqual(_parse_limit(None), 20)

    def test_limit_valid_passes_through(self):
        self.assertEqual(_parse_limit("50"), 50)

    def test_offset_negative_clamped_to_zero(self):
        self.assertEqual(_parse_offset("-5"), 0)

    def test_offset_invalid_falls_back_to_zero(self):
        self.assertEqual(_parse_offset("abc"), 0)

    def test_offset_missing_uses_zero(self):
        self.assertEqual(_parse_offset(None), 0)

    def test_offset_valid_passes_through(self):
        self.assertEqual(_parse_offset("40"), 40)


class GtcWaitingSinceTests(ContactListServiceTestsBase):
    """Nhóm Chăm sóc sau bán đếm chờ từ mốc chuyển GTC, không phải từ ngày data về."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-gtc")
        self.now = timezone.now()

    def _gtc_event(self, order, *, at, from_code="2"):
        event = OrderJourneyEvent.objects.create(
            order=order,
            event_type="order_status_changed",
            from_code=from_code,
            to_code=str(OrderStatus.DELIVERED_SUCCESS.value),
        )
        OrderJourneyEvent.objects.filter(pk=event.pk).update(created_at=at)
        return event

    def _item_for(self, customer):
        result = ContactListService.list_contacts(user=self.admin)
        items = [i for i in result["items"] if i["customer_id"] == customer.id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_delivered_order_waits_from_journey_event(self):
        customer = _customer(full_name="GTC Event", phone_raw="0921111111", phone_normalized="0921111111")
        order = self._order(
            customer,
            created_at=self.now - timedelta(days=11),
            status=OrderStatus.DELIVERED_SUCCESS,
        )
        gtc_at = self.now - timedelta(days=3)
        self._gtc_event(order, at=gtc_at)

        item = self._item_for(customer)

        self.assertEqual(item["waiting_basis"], "gtc")
        self.assertEqual(item["waiting_since"], gtc_at)

    def test_delivered_order_without_event_falls_back_to_updated_at(self):
        customer = _customer(full_name="GTC Fallback", phone_raw="0921111112", phone_normalized="0921111112")
        order = self._order(
            customer,
            created_at=self.now - timedelta(days=11),
            status=OrderStatus.DELIVERED_SUCCESS,
        )
        updated_at = self.now - timedelta(days=5)
        Order.objects.filter(pk=order.pk).update(updated_at=updated_at)

        item = self._item_for(customer)

        self.assertEqual(item["waiting_basis"], "gtc_estimated")
        self.assertEqual(item["waiting_since"], updated_at)

    def test_redelivered_order_uses_latest_gtc_event(self):
        """GTC → hoàn → GTC lại: đếm từ lần GTC đang hiệu lực, không phải lần đầu."""
        customer = _customer(full_name="GTC Lai", phone_raw="0921111113", phone_normalized="0921111113")
        order = self._order(
            customer,
            created_at=self.now - timedelta(days=20),
            status=OrderStatus.DELIVERED_SUCCESS,
        )
        self._gtc_event(order, at=self.now - timedelta(days=15))
        latest_gtc = self.now - timedelta(days=2)
        self._gtc_event(order, at=latest_gtc, from_code="4")

        item = self._item_for(customer)

        self.assertEqual(item["waiting_since"], latest_gtc)

    def test_non_delivered_order_keeps_activity_basis(self):
        customer = _customer(full_name="Chua GTC", phone_raw="0921111114", phone_normalized="0921111114")
        created_at = self.now - timedelta(days=4)
        self._order(
            customer,
            created_at=created_at,
            status=OrderStatus.IN_DELIVERY,
            sales_status_code="closed",
        )

        item = self._item_for(customer)

        self.assertEqual(item["waiting_basis"], "activity")
        self.assertEqual(item["waiting_since"], created_at)


class LatestOrderItemsAndAmountTests(ContactListServiceTestsBase):
    """latest_order mang SKU + tiền thực thu để CSKH biết khách đã mua gì, giá bao nhiêu."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-items")
        self.product_a = Product.objects.create(sku="SKU-A", name="San pham A")
        self.product_b = Product.objects.create(sku="SKU-B", name="San pham B")

    def _item_for(self, customer):
        result = ContactListService.list_contacts(user=self.admin)
        items = [i for i in result["items"] if i["customer_id"] == customer.id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_items_come_from_order_items(self):
        customer = _customer(full_name="Co OrderItem", phone_raw="0922222221", phone_normalized="0922222221")
        order = self._order(customer)
        Order.objects.filter(pk=order.pk).update(total_after_discount="1250000.00")
        OrderItem.objects.create(
            order=order, product=self.product_a, product_name="San pham A", sku="SKU-A", quantity=2
        )
        OrderItem.objects.create(
            order=order, product=self.product_b, product_name="San pham B", sku="SKU-B", quantity=1
        )

        latest = self._item_for(customer)["latest_order"]

        self.assertEqual(
            latest["items"],
            [
                {"sku": "SKU-A", "product_name": "San pham A", "quantity": 2},
                {"sku": "SKU-B", "product_name": "San pham B", "quantity": 1},
            ],
        )
        self.assertEqual(latest["total_after_discount"], "1250000.00")

    def test_order_without_items_falls_back_to_joined_sku_string(self):
        customer = _customer(full_name="Khong OrderItem", phone_raw="0922222222", phone_normalized="0922222222")
        order = self._order(customer)
        Order.objects.filter(pk=order.pk).update(sku="SKU-X, SKU-Y")

        latest = self._item_for(customer)["latest_order"]

        self.assertEqual(
            latest["items"],
            [
                {"sku": "SKU-X", "product_name": "", "quantity": 0},
                {"sku": "SKU-Y", "product_name": "", "quantity": 0},
            ],
        )

    def test_order_without_items_falls_back_to_comma_separated_sku_without_space(self):
        """import_excel.py:381 ghi Order.sku bằng ",".join(...) — không khoảng
        trắng, khác với order_service_helpers dùng ", ". Cả hai kiểu phải tách được."""
        customer = _customer(full_name="Khong Khoang Trang", phone_raw="0922222224", phone_normalized="0922222224")
        order = self._order(customer)
        Order.objects.filter(pk=order.pk).update(sku="SKU-M,SKU-N")

        latest = self._item_for(customer)["latest_order"]

        self.assertEqual(
            latest["items"],
            [
                {"sku": "SKU-M", "product_name": "", "quantity": 0},
                {"sku": "SKU-N", "product_name": "", "quantity": 0},
            ],
        )

    def test_order_without_items_and_without_sku_returns_empty_list(self):
        customer = _customer(full_name="Trong Tron", phone_raw="0922222223", phone_normalized="0922222223")
        self._order(customer)

        self.assertEqual(self._item_for(customer)["latest_order"]["items"], [])


class ProductFilterTests(ContactListServiceTestsBase):
    """Lọc theo sản phẩm — match bằng product_id trên OrderItem (không icontains)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-product")
        self.now = timezone.now()
        self.product_a = Product.objects.create(sku="PF-A", name="Loc A")
        self.product_b = Product.objects.create(sku="PF-B", name="Loc B")

    def _order_with(self, customer, product, *, created_at):
        order = self._order(customer, created_at=created_at)
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name, sku=product.sku, quantity=1
        )
        return order

    def test_filter_keeps_only_customers_owning_the_product(self):
        buyer = _customer(full_name="Mua A", phone_raw="0923333331", phone_normalized="0923333331")
        other = _customer(full_name="Mua B", phone_raw="0923333332", phone_normalized="0923333332")
        self._order_with(buyer, self.product_a, created_at=self.now - timedelta(days=1))
        self._order_with(other, self.product_b, created_at=self.now - timedelta(days=1))

        result = ContactListService.list_contacts(user=self.admin, product_ids=[self.product_a.id])

        self.assertEqual([i["customer_id"] for i in result["items"]], [buyer.id])

    def test_row_shows_latest_order_containing_the_product(self):
        """Khách mua A rồi mua B: lọc A phải trả đơn A, không phải đơn mới nhất (B)."""
        customer = _customer(full_name="Mua Ca Hai", phone_raw="0923333333", phone_normalized="0923333333")
        order_a = self._order_with(customer, self.product_a, created_at=self.now - timedelta(days=5))
        self._order_with(customer, self.product_b, created_at=self.now - timedelta(days=1))

        result = ContactListService.list_contacts(user=self.admin, product_ids=[self.product_a.id])

        self.assertEqual([i["latest_order"]["id"] for i in result["items"]], [order_a.id])

    def test_no_filter_returns_latest_order_regardless_of_product(self):
        customer = _customer(full_name="Khong Loc", phone_raw="0923333334", phone_normalized="0923333334")
        self._order_with(customer, self.product_a, created_at=self.now - timedelta(days=5))
        order_b = self._order_with(customer, self.product_b, created_at=self.now - timedelta(days=1))

        result = ContactListService.list_contacts(user=self.admin)
        items = [i for i in result["items"] if i["customer_id"] == customer.id]

        self.assertEqual(items[0]["latest_order"]["id"], order_b.id)


# ══════════════════════════════════════════════════════════════════════════════
# Cache tập enriched rows (perf/contact-list-cache) — TTL dùng chung
# ORDERS_LIST_COUNT_CACHE_TTL (mặc định 0 = tắt). Cache LocMem riêng từng test qua
# fixture `isolated_cache`, KHÔNG đụng Redis thật (mirror test_order_list_contract.py).
# ══════════════════════════════════════════════════════════════════════════════


# Cache rows: LocMemCache riêng từng test (backend/conftest.py).
@pytest.mark.usefixtures("isolated_cache")
class ContactRowsCacheTests(ContactListServiceTestsBase):
    def setUp(self):
        self.admin = make_admin("admin-contacts-cache")
        self.stage = OperationStage.objects.create(name="Cache Stage")
        now = timezone.now()
        self.customer_overdue = _customer(
            full_name="Cache Overdue", phone_raw="0941000001", phone_normalized="0941000001"
        )
        self._order(self.customer_overdue, created_at=now)
        CustomerOperationState.objects.create(
            customer=self.customer_overdue,
            current_stage=self.stage,
            due_at=now - timedelta(hours=1),
            entered_at=now,
        )
        self.customer_plain = _customer(
            full_name="Cache Plain", phone_raw="0941000002", phone_normalized="0941000002"
        )
        self._order(self.customer_plain, created_at=now)

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=120)
    def test_second_identical_request_serves_rows_from_cache(self):
        first = ContactListService.list_contacts(user=self.admin)
        # Cache hit → pipeline nặng (DISTINCT ON + enrich) không được chạy lại.
        with mock.patch.object(
            ContactListService,
            "_compute_enriched_rows",
            side_effect=AssertionError("cache hit không được chạy lại pipeline nặng"),
        ):
            second = ContactListService.list_contacts(user=self.admin)
        self.assertEqual(second["total"], first["total"])
        self.assertEqual(second["action_counts"], first["action_counts"])
        self.assertEqual(second["operation_counts"], first["operation_counts"])
        self.assertEqual(
            [i["customer_id"] for i in second["items"]],
            [i["customer_id"] for i in first["items"]],
        )

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=120)
    def test_python_filters_share_one_cache_entry(self):
        """Chip action_needed / operation_bucket / interaction là lọc PYTHON áp SAU
        cache — bấm chip khác nhau phải trúng chung entry, không chạy lại pipeline."""
        ContactListService.list_contacts(user=self.admin)
        with mock.patch.object(
            ContactListService,
            "_compute_enriched_rows",
            side_effect=AssertionError("đổi chip lọc Python không được chạy lại pipeline"),
        ):
            result = ContactListService.list_contacts(
                user=self.admin, operation_bucket="overdue"
            )
        self.assertEqual(
            {i["customer_id"] for i in result["items"]}, {self.customer_overdue.id}
        )
        # operation_counts vẫn đếm trên tập nền đầy đủ (2 khách).
        self.assertEqual(result["operation_counts"]["all"]["total"], 2)

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=120)
    def test_cache_key_separates_user_scope(self):
        """User scope OWN không được ăn entry của admin (scope ALL) dù bộ lọc y hệt."""
        own_user = _own_scope_user("sale-own-cache")
        own_customer = _customer(
            full_name="Own Cache", phone_raw="0941000003", phone_normalized="0941000003"
        )
        self._order(own_customer, staff=own_user)

        # Warm cache bằng admin trước — nếu key thiếu scope, own_user sẽ nhìn thấy
        # toàn bộ khách của admin (rò dữ liệu giữa sale).
        admin_result = ContactListService.list_contacts(user=self.admin)
        own_result = ContactListService.list_contacts(user=own_user)

        self.assertEqual(
            {i["customer_id"] for i in own_result["items"]}, {own_customer.id}
        )
        self.assertEqual(admin_result["total"], 3)
        self.assertEqual(own_result["total"], 1)

    def test_rows_cache_key_differs_between_own_users(self):
        kwargs = dict(
            date_from=None,
            date_to=None,
            q="",
            channel_ids=None,
            order_sources=None,
            staff_ids=None,
            product_ids=None,
            customer_ids=None,
        )
        key_1 = ContactListService._rows_cache_key(_own_scope_user("sale-own-k1"), **kwargs)
        key_2 = ContactListService._rows_cache_key(_own_scope_user("sale-own-k2"), **kwargs)
        self.assertTrue(key_1.startswith("orders:contact_rows:"))
        self.assertNotEqual(key_1, key_2)

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=0)
    def test_ttl_zero_keeps_cache_disabled(self):
        """Hành vi hiện tại khi env chưa bật TTL: pipeline chạy mỗi request, không
        đọc/ghi cache — giữ nguyên default 0 của ORDERS_LIST_COUNT_CACHE_TTL."""
        with mock.patch.object(
            ContactListService,
            "_compute_enriched_rows",
            wraps=ContactListService._compute_enriched_rows,
        ) as spy:
            ContactListService.list_contacts(user=self.admin)
            ContactListService.list_contacts(user=self.admin)
        self.assertEqual(spy.call_count, 2)

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=120)
    def test_new_order_within_ttl_is_stale_by_design(self):
        """TTL-only, không invalidate theo sự kiện (giống status_counts): đơn tạo
        trong TTL chưa hiện vào danh sách — độ trễ tối đa TTL giây, chấp nhận được."""
        first = ContactListService.list_contacts(user=self.admin)
        late_customer = _customer(
            full_name="Trong TTL", phone_raw="0941000004", phone_normalized="0941000004"
        )
        self._order(late_customer)
        second = ContactListService.list_contacts(user=self.admin)
        self.assertEqual(second["total"], first["total"])

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=120)
    def test_export_estimate_and_write_compute_pipeline_once(self):
        """estimate() + write() của job export cùng bộ lọc → chung một entry cache,
        pipeline nặng chỉ chạy MỘT lần thay vì hai."""
        filters = dict(
            date_from=None,
            date_to=None,
            q="",
            channel_ids=None,
            order_sources=None,
            staff_ids=None,
            product_ids=None,
            action_needed=None,
            interaction="",
            customer_type="",
            operation_bucket=None,
            customer_ids=None,
        )
        with mock.patch.object(
            ContactListService,
            "_compute_enriched_rows",
            wraps=ContactListService._compute_enriched_rows,
        ) as spy:
            total = ContactListService.count_contacts(user=self.admin, filters=filters)
            batches = list(
                ContactListService.iter_contact_batches(user=self.admin, filters=filters)
            )
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(total, 2)
        self.assertEqual(total, sum(len(batch) for batch in batches))


class RefreshOperationBucketsTests(SimpleTestCase):
    """bucket là trường duy nhất phụ thuộc thời điểm đọc — cache hit phải tính lại
    từ due_at; due_at=None ("none"/"ended") bất biến nên giữ nguyên giá trị cache."""

    def test_refreshes_time_dependent_buckets_only(self):
        from apps.orders.application.services.contact_list_operation import (
            refresh_operation_buckets,
        )

        now = timezone.now()
        rows = [
            # Lúc ghi cache còn "upcoming", sang thời điểm đọc đã quá hạn.
            {"operation": {"due_at": now - timedelta(hours=1), "bucket": "upcoming"}},
            {"operation": {"due_at": now + timedelta(days=2), "bucket": "upcoming"}},
            {"operation": {"due_at": None, "bucket": "none"}},
            {"operation": {"due_at": None, "bucket": "ended"}},
        ]
        refresh_operation_buckets(rows, now=now)
        self.assertEqual(rows[0]["operation"]["bucket"], "overdue")
        self.assertEqual(rows[1]["operation"]["bucket"], "upcoming")
        # KHÔNG được biến "none" thành "ended" (bucket_of(None) trả "ended").
        self.assertEqual(rows[2]["operation"]["bucket"], "none")
        self.assertEqual(rows[3]["operation"]["bucket"], "ended")


class ProductFilterApiTests(ContactListServiceTestsBase):
    """Query param ?product= đi tới service (parse multi-value như channel/staff)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-product-api")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.product = Product.objects.create(sku="API-A", name="Api A")
        self.other = Product.objects.create(sku="API-B", name="Api B")
        self.buyer = _customer(full_name="Api Mua A", phone_raw="0924444441", phone_normalized="0924444441")
        self.non_buyer = _customer(full_name="Api Mua B", phone_raw="0924444442", phone_normalized="0924444442")
        for customer, product in ((self.buyer, self.product), (self.non_buyer, self.other)):
            order = self._order(customer)
            OrderItem.objects.create(
                order=order, product=product, product_name=product.name, sku=product.sku, quantity=1
            )

    def test_product_query_param_filters_rows(self):
        response = self.client.get("/api/sale/contacts", {"product": str(self.product.id)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [i["customer_id"] for i in response.json()["items"]], [self.buyer.id]
        )
