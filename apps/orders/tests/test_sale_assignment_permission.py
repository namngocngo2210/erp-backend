"""Quyền `sale-assignment:edit` gác mọi đường ghi trường `staff` của đơn.

Bốn đường: PATCH đơn, POST tạo đơn, import Excel workbook, import Excel text.
Thiếu một đường là hở toàn bộ — sale chỉ cần đổi cách thao tác.
"""

from io import BytesIO

from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.import_excel import IMPORT_TEMPLATE_HEADERS, import_orders_from_text, import_orders_from_workbook
from apps.orders.import_landing_excel import (
    LANDING_IMPORT_TEMPLATE_HEADERS,
    import_landing_orders_from_text,
    import_landing_orders_from_workbook,
)
from apps.orders.models import Order, OrderStatus
from apps.users.constants import DepartmentID
from apps.users.models import Department, User
from apps.users.tests.helpers import (
    grant_order_fields,
    make_user_with_permissions,
    make_user_with_sections,
)


def _build_import_workbook(headers: list[str], row: list[str]) -> BytesIO:
    """Xlsx tối thiểu trong bộ nhớ dùng cho test import_*_from_workbook — không mock openpyxl."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers)
    worksheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


class SaleAssignmentOrderPatchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="sa-patch", name="Sale Assign Patch")
        self.sale_a = make_user_with_sections("sa-patch-a", ["orders"])
        self.sale_b = User.objects.create_user(username="sa-patch-b", password="x")
        UserChannel.objects.create(user=self.sale_a, channel=self.channel, is_active=True)
        customer = Customer.objects.create(phone_raw="0909000001", phone_normalized="0909000001")
        self.order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            staff=self.sale_a,
            name="Khach Patch",
            phone="0909000001",
            status=OrderStatus.NEW,
        )
        # Ý định của lớp test này là đo quyền sale-assignment (staff_id), không phải
        # quyền field (Task 7) — cấp "name" để 2 test PATCH field khác staff_id không
        # bị guard field chặn oan, mất mục tiêu ban đầu của test.
        grant_order_fields(self.sale_a, "name")

    def test_patch_staff_forbidden_without_permission(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": self.sale_b.id}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.staff_id, self.sale_a.id)

    def test_patch_same_staff_allowed_without_permission(self):
        # Form đơn gửi lại full object; gửi đúng giá trị cũ KHÔNG được coi là đổi sale.
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"staff_id": self.sale_a.id, "name": "Khach Patch 2"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.name, "Khach Patch 2")

    def test_patch_other_fields_allowed_without_permission(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"name": "Khach Doi Ten"}, format="json"
        )

        self.assertEqual(response.status_code, 200)

    def test_patch_staff_allowed_with_permission(self):
        manager = make_user_with_permissions(
            "sa-patch-manager",
            [
                "orders:view",
                "orders:edit",
                "orders:create",
                "orders:delete",
                "sale-assignment:edit",
            ],
        )
        UserChannel.objects.create(user=manager, channel=self.channel, is_active=True)
        self.client.force_authenticate(user=manager)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": self.sale_b.id}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.staff_id, self.sale_b.id)

    def test_clearing_staff_forbidden_without_permission(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": None}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.staff_id, self.sale_a.id)

    def test_patch_staff_on_unassigned_order_forbidden_without_permission(self):
        self.order.staff = None
        self.order.save(update_fields=["staff"])
        self.client.force_authenticate(user=self.sale_a)
        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": f" {self.sale_a.id}"}, format="json"
        )
        self.assertEqual(response.status_code, 403)


class SaleAssignmentOrderCreateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="sa-create", name="Sale Assign Create")
        self.sale_a = make_user_with_sections("sa-create-a", ["orders"])
        self.sale_b = User.objects.create_user(username="sa-create-b", password="x")
        UserChannel.objects.create(user=self.sale_a, channel=self.channel, is_active=True)

    def _payload(self, **overrides):
        payload = {
            "channel_code": self.channel.code,
            "name": "Khach Create",
            "phone": "0909000002",
            "raw_address": "1 Nguyen Trai, Quan 1, HCM",
            "status": OrderStatus.NEW,
            "total_amount": "100000",
        }
        payload.update(overrides)
        return payload

    def test_create_forced_to_self_when_staff_omitted(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.post("/api/orders/create", self._payload(), format="json")

        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.staff_id, self.sale_a.id)

    def test_create_with_self_as_staff_allowed(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.post(
            "/api/orders/create", self._payload(staff_id=self.sale_a.id), format="json"
        )

        self.assertEqual(response.status_code, 201)

    def test_create_for_other_staff_forbidden(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.post(
            "/api/orders/create", self._payload(staff_id=self.sale_b.id), format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Order.objects.filter(phone="0909000002").exists())

    def test_create_for_other_staff_allowed_with_permission(self):
        manager = make_user_with_permissions(
            "sa-create-manager",
            ["orders:view", "orders:create", "orders:edit", "sale-assignment:edit"],
        )
        UserChannel.objects.create(user=manager, channel=self.channel, is_active=True)
        self.client.force_authenticate(user=manager)

        response = self.client.post(
            "/api/orders/create", self._payload(staff_id=self.sale_b.id), format="json"
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.staff_id, self.sale_b.id)


class SaleAssignmentImportTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="sa-import", name="Sale Assign Import")
        self.sale_a = make_user_with_sections("sa-import-a", ["orders"])
        self.sale_b = User.objects.create_user(username="sa-import-b", password="x")
        UserChannel.objects.create(user=self.sale_a, channel=self.channel, is_active=True)

    def _row(self, staff_username: str) -> str:
        # Thứ tự cột theo docstring import_orders_from_text:
        # kiotviet_channel_id|staff_username|channel_code|name|customer_gender|phone|
        # raw_address|sku_list|quantity_list|price_list|discount_amount|
        # booking_voucher_discount|weight|dimensions|branch_id|order_type|customer_prepaid
        return (
            f"|{staff_username}|{self.channel.code}|Khach Import||0909000003|"
            "1 Nguyen Trai, Quan 1, HCM|||||||||"
        )

    def test_import_ignores_staff_column_without_permission(self):
        result = import_orders_from_text(
            text_data=self._row(self.sale_b.username), actor=self.sale_a
        )

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000003")
        self.assertEqual(order.staff_id, self.sale_a.id)

    def test_import_reports_warning_when_staff_column_ignored(self):
        result = import_orders_from_text(
            text_data=self._row(self.sale_b.username), actor=self.sale_a
        )

        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Nhân viên", result["warnings"][0])

    def test_import_no_warning_when_column_empty(self):
        result = import_orders_from_text(text_data=self._row(""), actor=self.sale_a)

        self.assertEqual(result["warnings"], [])

    def test_import_honours_staff_column_with_permission(self):
        manager = make_user_with_permissions(
            "sa-import-manager",
            ["orders:view", "orders:create", "orders:edit", "sale-assignment:edit"],
        )
        UserChannel.objects.create(user=manager, channel=self.channel, is_active=True)

        result = import_orders_from_text(
            text_data=self._row(self.sale_b.username), actor=manager
        )

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000003")
        self.assertEqual(order.staff_id, self.sale_b.id)
        self.assertEqual(result["warnings"], [])

    def _workbook_row(self, staff_username: str, phone: str) -> list[str]:
        # Thứ tự cột theo IMPORT_TEMPLATE_HEADERS (apps/orders/_import_excel_constants.py):
        # Kênh Kiot|Mã nhân viên|Kênh Bán|Tên KH|Giới Tính|SĐT|Địa chỉ|Mã SP|SL|Giá|
        # Giảm Giá|Giảm Giá Phiếu Đặt|Trọng Lượng|Kích Thước|Kho|Loại Đơn|Khách TT Trước
        return [
            "",
            staff_username,
            self.channel.code,
            "Khach Import Workbook",
            "",
            phone,
            "1 Nguyen Trai, Quan 1, HCM",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]

    def test_workbook_import_ignores_staff_column_without_permission(self):
        workbook = _build_import_workbook(
            IMPORT_TEMPLATE_HEADERS, self._workbook_row(self.sale_b.username, "0909000005")
        )

        result = import_orders_from_workbook(file_obj=workbook, actor=self.sale_a)

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000005")
        self.assertEqual(order.staff_id, self.sale_a.id)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Nhân viên", result["warnings"][0])

    def test_workbook_import_honours_staff_column_with_permission(self):
        manager = make_user_with_permissions(
            "sa-import-workbook-manager",
            ["orders:view", "orders:create", "orders:edit", "sale-assignment:edit"],
        )
        UserChannel.objects.create(user=manager, channel=self.channel, is_active=True)
        workbook = _build_import_workbook(
            IMPORT_TEMPLATE_HEADERS, self._workbook_row(self.sale_b.username, "0909000005")
        )

        result = import_orders_from_workbook(file_obj=workbook, actor=manager)

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000005")
        self.assertEqual(order.staff_id, self.sale_b.id)
        self.assertEqual(result["warnings"], [])

    def _landing_row(self, staff_username: str, phone: str) -> str:
        # Thứ tự cột theo docstring import_landing_orders_from_text:
        # channel_code|staff_username|created_at|name|phone|raw_address|product_raw
        return f"{self.channel.code}|{staff_username}||Khach Landing Import|{phone}|1 Nguyen Trai, Quan 1, HCM|"

    def test_landing_text_import_ignores_staff_column_without_permission(self):
        result = import_landing_orders_from_text(
            text_data=self._landing_row(self.sale_b.username, "0909000006"), actor=self.sale_a
        )

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000006")
        self.assertEqual(order.staff_id, self.sale_a.id)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Nhân viên", result["warnings"][0])

    def test_landing_text_import_honours_staff_column_with_permission(self):
        manager = make_user_with_permissions(
            "sa-import-landing-text-manager",
            ["orders:view", "orders:create", "orders:edit", "sale-assignment:edit"],
        )

        result = import_landing_orders_from_text(
            text_data=self._landing_row(self.sale_b.username, "0909000006"), actor=manager
        )

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000006")
        self.assertEqual(order.staff_id, self.sale_b.id)
        self.assertEqual(result["warnings"], [])

    def _landing_workbook_row(self, staff_username: str, phone: str) -> list[str]:
        # Thứ tự cột theo LANDING_IMPORT_TEMPLATE_HEADERS:
        # Kênh Bán|Mã nhân viên|Thời gian|Tên KH|SĐT|Địa chỉ|Tên SP
        return [self.channel.code, staff_username, "", "Khach Landing Workbook", phone, "1 Nguyen Trai, Quan 1, HCM", ""]

    def test_landing_workbook_import_ignores_staff_column_without_permission(self):
        workbook = _build_import_workbook(
            LANDING_IMPORT_TEMPLATE_HEADERS, self._landing_workbook_row(self.sale_b.username, "0909000007")
        )

        result = import_landing_orders_from_workbook(file_obj=workbook, actor=self.sale_a)

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000007")
        self.assertEqual(order.staff_id, self.sale_a.id)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Nhân viên", result["warnings"][0])

    def test_landing_workbook_import_honours_staff_column_with_permission(self):
        manager = make_user_with_permissions(
            "sa-import-landing-workbook-manager",
            ["orders:view", "orders:create", "orders:edit", "sale-assignment:edit"],
        )
        workbook = _build_import_workbook(
            LANDING_IMPORT_TEMPLATE_HEADERS, self._landing_workbook_row(self.sale_b.username, "0909000007")
        )

        result = import_landing_orders_from_workbook(file_obj=workbook, actor=manager)

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000007")
        self.assertEqual(order.staff_id, self.sale_b.id)
        self.assertEqual(result["warnings"], [])


class SaleAssignmentImportTextHttpTests(TestCase):
    """Kiểm HTTP: warnings phải lọt tới response body của endpoint import-text.

    Các test khác trong file này gọi thẳng import_orders_from_text() nên chưa từng
    xác nhận view pass-through result nguyên vẹn (T2, audit 2026-08-07).
    """

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="sa-import-http", name="Sale Assign Import HTTP")
        self.sale_a = make_user_with_sections("sa-import-http-a", ["orders"])
        self.sale_b = User.objects.create_user(username="sa-import-http-b", password="x")
        UserChannel.objects.create(user=self.sale_a, channel=self.channel, is_active=True)

    def test_import_text_endpoint_returns_warnings_in_response_body(self):
        self.client.force_authenticate(user=self.sale_a)
        row = (
            f"|{self.sale_b.username}|{self.channel.code}|Khach Import Http||0909000008|"
            "1 Nguyen Trai, Quan 1, HCM|||||||||"
        )

        response = self.client.post("/api/orders/import-text", {"text": row}, format="json")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.data["warnings"]), 1)
        self.assertIn("Nhân viên", response.data["warnings"][0])


class LeadAssignmentTransferPermissionTests(TestCase):
    """I1: lead_assignment_transfer bulk-update Order.staff nhưng chỉ gác bằng
    require_section("lead-assignment") (= :view) — thiếu sale-assignment:edit
    (audit 2026-08-07)."""

    URL = "/api/orders/lead-assignment/transfer"

    def setUp(self):
        self.client = APIClient()
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        self.from_user = User.objects.create_user(username="la-transfer-from", password="x")
        self.to_user = User.objects.create_user(
            username="la-transfer-to",
            password="x",
            department_id=DepartmentID.SALES,
            is_active=True,
        )

    def _payload(self):
        return {
            "sheet": "sheet1",
            "from_user_id": self.from_user.id,
            "to_user_id": self.to_user.id,
            "count": 1,
        }

    def test_transfer_forbidden_with_view_only_permission(self):
        viewer = make_user_with_sections("la-transfer-viewer", ["lead-assignment"])
        self.client.force_authenticate(user=viewer)

        response = self.client.post(self.URL, self._payload(), format="json")

        self.assertEqual(response.status_code, 403)

    def test_transfer_not_forbidden_with_sale_assignment_permission(self):
        manager = make_user_with_permissions(
            "la-transfer-manager", ["lead-assignment:view", "sale-assignment:edit"]
        )
        self.client.force_authenticate(user=manager)

        response = self.client.post(self.URL, self._payload(), format="json")

        self.assertNotEqual(response.status_code, 403)
