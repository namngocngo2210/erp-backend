import pytest
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.users.constants import DepartmentID
from apps.users.models import Department
from apps.users.tests.helpers import make_admin, make_user_with_sections

STAFF_URL = "/api/channels/staff"


class ChannelListScopeTests(TestCase):
    """Scope danh sách kênh theo phòng ban (Lớp B - design 5.1)."""

    def setUp(self):
        self.client = APIClient()
        self.sales = Department.objects.create(id=DepartmentID.SALES, name="Sales")
        self.ads = Department.objects.create(id=DepartmentID.ADS, name="Ads")
        self.ch_sales = Channel.objects.create(code="ch-sales", name="Sales Channel")
        self.ch_other = Channel.objects.create(code="ch-other", name="Other Channel")
        self.ch_shared = Channel.objects.create(code="ch-shared", name="Shared Channel")
        self.ch_sales.departments.add(self.sales)
        self.ch_other.departments.add(self.ads)
        # ch_shared: không gán phòng ban nào ⇒ dùng chung.

    def _codes(self, user):
        self.client.force_authenticate(user=user)
        resp = self.client.get("/api/channels?limit=100&offset=0")
        self.assertEqual(resp.status_code, 200, resp.content)
        return {row["code"] for row in resp.json()}

    def test_admin_sees_all_channels(self):
        admin = make_admin("adm")
        self.assertEqual(self._codes(admin), {"ch-sales", "ch-other", "ch-shared"})

    def test_sales_staff_sees_own_dept_and_shared(self):
        sales_staff = make_user_with_sections("sale", ["orders"])
        sales_staff.department = self.sales
        sales_staff.save()
        self.assertEqual(self._codes(sales_staff), {"ch-sales", "ch-shared"})

    def test_ads_staff_sees_only_assigned(self):
        ads_staff = make_user_with_sections("ads", ["ads-report"])
        ads_staff.department = self.ads
        ads_staff.save()
        UserChannel.objects.create(user=ads_staff, channel=self.ch_other, is_active=True)
        self.assertEqual(self._codes(ads_staff), {"ch-other"})

    def test_no_dept_staff_sees_all(self):
        nodept = make_user_with_sections("nodept", ["orders"])
        self.assertEqual(self._codes(nodept), {"ch-sales", "ch-other", "ch-shared"})


# Cô lập cache khỏi Redis live: endpoint cache 30s theo scope_key — fixture
# `isolated_cache` (backend/conftest.py) cấp LocMemCache riêng cho từng test.
@pytest.mark.usefixtures("isolated_cache")
class StaffCollectionScopeTests(TestCase):
    """Scope danh sách nhân viên cho dropdown đơn (endpoint /api/channels/staff).

    Trọng tâm: tham số `?department=` cho phép nhân viên phòng khác (vd Ads) vẫn thấy
    đúng nhân viên phòng Sales/Booking, độc lập với phòng ban người xem.
    """

    def setUp(self):
        self.client = APIClient()
        self.sales = Department.objects.create(id=DepartmentID.SALES, name="Sales")
        self.ads = Department.objects.create(id=DepartmentID.ADS, name="Ads")
        self.booking = Department.objects.create(id=DepartmentID.BOOKING, name="Booking")

        self.sales_staff = self._staff("sales1", self.sales)
        self.ads_staff = self._staff("ads1", self.ads)
        self.booking_staff = self._staff("book1", self.booking)

    def _staff(self, username, department):
        user = make_user_with_sections(username, ["orders"])
        user.department = department
        user.save()
        return user

    def _usernames(self, user, query=""):
        self.client.force_authenticate(user=user)
        resp = self.client.get(f"{STAFF_URL}{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return {row["username"] for row in resp.json()}

    def test_ads_viewer_without_param_sees_only_own_department(self):
        # Hành vi cũ giữ nguyên: không có param -> scope theo phòng người xem.
        self.assertEqual(self._usernames(self.ads_staff), {"ads1"})

    def test_ads_viewer_with_sales_param_sees_sales_staff(self):
        # Fix: nhân viên Ads yêu cầu phòng Sales -> thấy đúng nhân viên Sales.
        self.assertEqual(
            self._usernames(self.ads_staff, f"?department={DepartmentID.SALES}"),
            {"sales1"},
        )

    def test_koc_page_param_returns_booking_staff(self):
        self.assertEqual(
            self._usernames(self.sales_staff, f"?department={DepartmentID.BOOKING}"),
            {"book1"},
        )

    def test_sales_viewer_with_sales_param_sees_sales_staff(self):
        self.assertEqual(
            self._usernames(self.sales_staff, f"?department={DepartmentID.SALES}"),
            {"sales1"},
        )

    def test_admin_without_param_sees_all_staff(self):
        admin = make_admin("adm")
        # Admin (role hệ thống) bị loại khỏi danh sách nhân viên, chỉ thấy 3 staff.
        self.assertEqual(
            self._usernames(admin), {"sales1", "ads1", "book1"}
        )

    def test_invalid_department_param_returns_400(self):
        self.client.force_authenticate(user=self.ads_staff)
        resp = self.client.get(f"{STAFF_URL}?department=abc")
        self.assertEqual(resp.status_code, 400, resp.content)


@pytest.mark.usefixtures("isolated_cache")
class StaffCollectionLimitTests(TestCase):
    """Trần dòng `?limit=` cho /api/channels/staff (audit 2026-09-23 L6)."""

    def setUp(self):
        self.client = APIClient()
        self.sales = Department.objects.create(id=DepartmentID.SALES, name="Sales")
        for name in ("s-a", "s-b", "s-c"):
            user = make_user_with_sections(name, ["orders"])
            user.department = self.sales
            user.save()
        self.client.force_authenticate(user=make_admin("staff-limit-admin"))

    def test_default_returns_everything(self):
        resp = self.client.get(STAFF_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()), 3)

    def test_limit_caps_rows_and_cache_key_is_per_limit(self):
        self.assertEqual(len(self.client.get(f"{STAFF_URL}?limit=1").json()), 1)
        self.assertEqual(len(self.client.get(f"{STAFF_URL}?limit=2").json()), 2)

    def test_invalid_limit_is_400(self):
        self.assertEqual(self.client.get(f"{STAFF_URL}?limit=abc").status_code, 400)
        self.assertEqual(self.client.get(f"{STAFF_URL}?limit=501").status_code, 400)
