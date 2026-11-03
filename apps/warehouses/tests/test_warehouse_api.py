"""Baseline smoke tests for apps.warehouses — list/create/update/delete API +
RBAC gate (require_section("warehouses")) + model sanity.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.tests.helpers import make_user_with_permissions
from apps.warehouses.models import Warehouse


class WarehouseApiRbacTests(TestCase):
    def setUp(self):
        # Audit 2026-08-19 N2: endpoint ghi nay doi quyen ghi that — user "authorized"
        # cua bo test nay dai dien cho role quan ly kho co du CRUD.
        self.authorized = make_user_with_permissions(
            "wh-authorized",
            ["warehouses:view", "warehouses:create", "warehouses:edit", "warehouses:delete"],
        )
        self.unauthorized = make_user_with_permissions("wh-unauthorized", ["orders:view"])
        self.client = APIClient()

    def test_list_warehouses_authorized_returns_200(self):
        Warehouse.objects.create(code="W1", name="Kho 1")
        self.client.force_authenticate(self.authorized)

        resp = self.client.get("/api/warehouses")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["code"], "W1")

    def test_list_warehouses_unauthorized_user_returns_403(self):
        # User has some RBAC permission but not "warehouses:view" — scope_for
        # defaults to OWN/none for ungranted sections, so this must be denied.
        self.client.force_authenticate(self.unauthorized)

        resp = self.client.get("/api/warehouses")

        self.assertEqual(resp.status_code, 403)

    def test_view_only_user_cannot_write(self):
        """Audit 2026-08-19 N2: quyen xem khong duoc tao/sua/xoa kho."""
        viewer = make_user_with_permissions("wh-viewer", ["warehouses:view"])
        self.client.force_authenticate(viewer)
        wh = Warehouse.objects.create(code="WV", name="Kho V")

        self.assertEqual(
            self.client.post("/api/warehouses", data={"code": "W9", "name": "Kho 9"}, format="json").status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(f"/api/warehouses/{wh.id}", data={"name": "Doi ten"}, format="json").status_code,
            403,
        )
        self.assertEqual(self.client.delete(f"/api/warehouses/{wh.id}").status_code, 403)

    def test_create_warehouse_authorized_returns_201(self):
        self.client.force_authenticate(self.authorized)

        resp = self.client.post(
            "/api/warehouses", data={"code": "W2", "name": "Kho 2"}, format="json"
        )

        self.assertEqual(resp.status_code, 201, resp.json())
        self.assertTrue(Warehouse.objects.filter(code="W2").exists())

    def test_create_warehouse_duplicate_code_returns_400(self):
        Warehouse.objects.create(code="W3", name="Kho 3")
        self.client.force_authenticate(self.authorized)

        resp = self.client.post(
            "/api/warehouses", data={"code": "W3", "name": "Kho khac"}, format="json"
        )

        self.assertEqual(resp.status_code, 400)

    def test_delete_warehouse_soft_deletes_and_deactivates(self):
        wh = Warehouse.objects.create(code="W4", name="Kho 4")
        self.client.force_authenticate(self.authorized)

        resp = self.client.delete(f"/api/warehouses/{wh.id}")

        self.assertEqual(resp.status_code, 204)
        wh.refresh_from_db()
        self.assertIsNotNone(wh.deleted_at)
        self.assertFalse(wh.is_active)


class WarehouseModelTests(TestCase):
    def test_str_returns_code_and_name(self):
        wh = Warehouse.objects.create(code="W9", name="Kho chinh")
        self.assertEqual(str(wh), "W9 - Kho chinh")

    def test_duplicate_code_raises_integrity_error_at_db_level(self):
        Warehouse.objects.create(code="DUP", name="Kho A")
        with self.assertRaises(IntegrityError):
            Warehouse.objects.create(code="DUP", name="Kho B")

    def test_soft_deleted_code_and_name_can_be_reused(self):
        """Audit 2026-09-23 C8: unique chỉ áp lên kho chưa xoá mềm."""
        old = Warehouse.objects.create(code="RE", name="Kho tái sử dụng")
        Warehouse.objects.filter(pk=old.pk).update(deleted_at=timezone.now())
        new = Warehouse.objects.create(code="RE", name="Kho tái sử dụng")
        self.assertNotEqual(old.pk, new.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Warehouse.objects.create(code="RE", name="Kho khác")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Warehouse.objects.create(code="RE2", name="Kho tái sử dụng")
