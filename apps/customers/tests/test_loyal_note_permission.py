"""Quyền trên endpoint ghi note khách thân thiết.

Trước đây endpoint gate bằng `loyal-customers:view` — ai xem được là ghi được toàn
bộ ghi chú, kể cả gán sale phụ trách. Nay: ghi cần `loyal-customers:edit`, riêng
trường `sale_staff_id` cần thêm `sale-assignment:edit`.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.customers.models import Customer, LoyalCustomerNote
from apps.users.models import User
from apps.users.tests.helpers import make_user_with_permissions


class LoyalNotePermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(
            full_name="Khach Than Thiet", phone_raw="0909000004", phone_normalized="0909000004"
        )
        self.sale = User.objects.create_user(username="loyal-sale", password="x")

    def _url(self) -> str:
        return f"/api/customers/loyal/{self.customer.id}/note"

    def test_viewer_cannot_write_note(self):
        viewer = make_user_with_permissions("loyal-viewer", ["loyal-customers:view"])
        self.client.force_authenticate(user=viewer)

        response = self.client.patch(self._url(), {"special_note": "abc"}, format="json")

        self.assertEqual(response.status_code, 403)

    def test_editor_can_write_note_without_sale_field(self):
        editor = make_user_with_permissions(
            "loyal-editor", ["loyal-customers:view", "loyal-customers:edit"]
        )
        self.client.force_authenticate(user=editor)

        response = self.client.patch(self._url(), {"special_note": "abc"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["special_note"], "abc")

    def test_editor_cannot_assign_sale(self):
        editor = make_user_with_permissions(
            "loyal-editor-2", ["loyal-customers:view", "loyal-customers:edit"]
        )
        self.client.force_authenticate(user=editor)

        response = self.client.patch(
            self._url(), {"sale_staff_id": self.sale.id}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(LoyalCustomerNote.objects.filter(customer=self.customer).exists())

    def test_manager_can_assign_sale(self):
        manager = make_user_with_permissions(
            "loyal-manager",
            ["loyal-customers:view", "loyal-customers:edit", "sale-assignment:edit"],
        )
        self.client.force_authenticate(user=manager)

        response = self.client.patch(
            self._url(), {"sale_staff_id": self.sale.id}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sale_staff_id"], self.sale.id)

    def test_editor_can_resend_null_sale_staff_id_with_other_field(self):
        # FE luôn gửi kèm sale_staff_id (null khi chưa gán). Editor không có
        # sale-assignment:edit vẫn phải lưu được special_note trong trường hợp này
        # (C1 audit 2026-08-07 — presence check cũ chặn oan mọi lượt lưu).
        editor = make_user_with_permissions(
            "loyal-editor-3", ["loyal-customers:view", "loyal-customers:edit"]
        )
        self.client.force_authenticate(user=editor)

        response = self.client.patch(
            self._url(),
            {"special_note": "ghi chu moi", "sale_staff_id": None},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["special_note"], "ghi chu moi")

    def test_editor_can_resend_unchanged_sale_staff_id(self):
        # Khách đã có note với sale gán sẵn; editor gửi lại ĐÚNG sale đó (không đổi)
        # kèm field khác → không được coi là "đổi sale" nên không bị 403.
        LoyalCustomerNote.objects.create(customer=self.customer, assigned_sale=self.sale)
        editor = make_user_with_permissions(
            "loyal-editor-4", ["loyal-customers:view", "loyal-customers:edit"]
        )
        self.client.force_authenticate(user=editor)

        response = self.client.patch(
            self._url(),
            {"special_note": "cap nhat", "sale_staff_id": self.sale.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["special_note"], "cap nhat")
        self.assertEqual(response.data["sale_staff_id"], self.sale.id)
