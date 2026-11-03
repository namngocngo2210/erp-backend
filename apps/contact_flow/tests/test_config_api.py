"""API test CRUD cấu hình Tác nghiệp CSKH + import Excel (task S1-4).

Xem .superpowers/sdd/task-S1-4-brief.md — 7 case bắt buộc.
"""
from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pytest
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.contact_flow.care_stages import SEEDED_CARE_STAGE_NAMES
from apps.contact_flow.models import OperationResult, OperationStage, OperationTransition
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections

FIXTURE = Path(__file__).parent / "fixtures" / "ImportTacNghiep.xlsx"


class ContactFlowConfigApiTests(TestCase):
    def setUp(self):
        # Migration 0002 seed sẵn 5 bước chăm sóc; các phép đếm tuyệt đối bên dưới giả
        # định cấu hình rỗng lúc bắt đầu (xem docstring `_clear_seeded_care_stages`).
        OperationStage.objects.filter(name__in=SEEDED_CARE_STAGE_NAMES).delete()
        self.client = APIClient()
        self.viewer = make_user_with_permissions("cf-viewer", ["contact_flow:view"])
        self.editor = make_user_with_sections("cf-editor", ["contact_flow"])

    # -- case 1: auth + RBAC gate on GET ---------------------------------
    def test_get_stages_requires_auth_and_view_permission(self):
        response = self.client.get("/api/contact-flow/stages")
        self.assertEqual(response.status_code, 401)

        no_access = make_user_with_sections("cf-no-access", [])
        self.client.force_authenticate(user=no_access)
        response = self.client.get("/api/contact-flow/stages")
        self.assertEqual(response.status_code, 403)

    # -- case 2: create with edit permission, visible in GET -------------
    def test_post_stage_with_edit_permission_then_visible_in_get(self):
        self.client.force_authenticate(user=self.editor)
        response = self.client.post(
            "/api/contact-flow/stages",
            {"name": "Gọi lần đầu", "color": "#ff0000", "is_start": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "Gọi lần đầu")
        self.assertTrue(body["is_start"])

        get_response = self.client.get("/api/contact-flow/stages")
        self.assertEqual(get_response.status_code, 200)
        names = [s["name"] for s in get_response.json()]
        self.assertIn("Gọi lần đầu", names)

    # -- case 3: transition to_stage set but delay missing -> 400 --------
    def test_post_transition_missing_delay_when_to_stage_set_returns_400(self):
        s1 = OperationStage.objects.create(name="Bước A")
        s2 = OperationStage.objects.create(name="Bước B")
        result = OperationResult.objects.create(name="Không nghe máy")

        self.client.force_authenticate(user=self.editor)
        response = self.client.post(
            "/api/contact-flow/transitions",
            {"from_stage_id": s1.id, "result_id": result.id, "to_stage_id": s2.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(OperationTransition.objects.count(), 0)

    # -- case 4: import real fixture -> 200, counts 34/12/131 ------------
    def test_import_real_fixture_returns_200_with_counts(self):
        self.client.force_authenticate(user=self.editor)
        with FIXTURE.open("rb") as f:
            response = self.client.post(
                "/api/contact-flow/import", {"file": f}, format="multipart"
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stages"]["created"], 34)
        self.assertEqual(body["results"]["created"], 12)
        self.assertEqual(body["transitions"]["created"], 131)
        self.assertEqual(OperationStage.objects.count(), 34)
        self.assertEqual(OperationResult.objects.count(), 12)
        self.assertEqual(OperationTransition.objects.count(), 131)

    # -- case 5: import file with error -> 400 {errors: [...]}, DB clean -
    def test_import_bad_file_returns_400_with_errors_and_db_clean(self):
        workbook = openpyxl.load_workbook(FIXTURE)
        workbook["tacnghiepcauhinh"].cell(row=4, column=2, value="Bước không tồn tại")
        buf = io.BytesIO()
        workbook.save(buf)
        buf.seek(0)
        buf.name = "bad.xlsx"

        self.client.force_authenticate(user=self.editor)
        response = self.client.post(
            "/api/contact-flow/import", {"file": buf}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("errors", body)
        self.assertTrue(len(body["errors"]) > 0)
        self.assertEqual(OperationStage.objects.count(), 0)
        self.assertEqual(OperationResult.objects.count(), 0)
        self.assertEqual(OperationTransition.objects.count(), 0)

    # -- case 6: DELETE transition -> 204; PATCH stage is_active -> 200 --
    def test_delete_transition_204_and_patch_stage_is_active_false_200(self):
        s1 = OperationStage.objects.create(name="Bước C")
        s2 = OperationStage.objects.create(name="Bước D")
        result = OperationResult.objects.create(name="Đã chốt")
        transition = OperationTransition.objects.create(
            from_stage=s1, result=result, to_stage=s2, delay_minutes=30
        )

        self.client.force_authenticate(user=self.editor)
        delete_response = self.client.delete(f"/api/contact-flow/transitions/{transition.id}")
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(OperationTransition.objects.filter(id=transition.id).exists())

        patch_response = self.client.patch(
            f"/api/contact-flow/stages/{s1.id}", {"is_active": False}, format="json"
        )
        self.assertEqual(patch_response.status_code, 200)
        s1.refresh_from_db()
        self.assertFalse(s1.is_active)

    # -- case 7: second is_start stage rejected --------------------------
    def test_second_is_start_stage_rejected_400(self):
        OperationStage.objects.create(name="Bước khởi đầu 1", is_start=True)

        self.client.force_authenticate(user=self.editor)
        response = self.client.post(
            "/api/contact-flow/stages",
            {"name": "Bước khởi đầu 2", "is_start": True},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OperationStage.objects.filter(name="Bước khởi đầu 2").exists())

    # -- case 8: view-only user (contact_flow:view, no edit) -> 403 on all
    # write endpoints; edit-permission check must reject before any DB write
    # (and, per config_views._update, before object lookup) -----------------
    def test_view_only_user_forbidden_on_all_write_endpoints(self):
        stage = OperationStage.objects.create(name="Bước view-only 1")
        stage2 = OperationStage.objects.create(name="Bước view-only 2")
        result = OperationResult.objects.create(name="KQ view-only")
        transition = OperationTransition.objects.create(
            from_stage=stage, result=result, to_stage=stage2, delay_minutes=15
        )

        self.client.force_authenticate(user=self.viewer)

        cases = [
            ("post", "/api/contact-flow/stages", {"name": "Bước mới"}),
            ("patch", f"/api/contact-flow/stages/{stage.id}", {"is_active": False}),
            ("post", "/api/contact-flow/results", {"name": "KQ mới"}),
            ("patch", f"/api/contact-flow/results/{result.id}", {"name": "KQ chỉnh sửa"}),
            (
                "post",
                "/api/contact-flow/transitions",
                {"from_stage_id": stage.id, "result_id": result.id},
            ),
            ("patch", f"/api/contact-flow/transitions/{transition.id}", {"delay_minutes": 5}),
            ("delete", f"/api/contact-flow/transitions/{transition.id}", None),
        ]
        for method, url, payload in cases:
            client_method = getattr(self.client, method)
            response = (
                client_method(url) if payload is None else client_method(url, payload, format="json")
            )
            self.assertEqual(response.status_code, 403, f"{method.upper()} {url}")

        with FIXTURE.open("rb") as f:
            import_response = self.client.post(
                "/api/contact-flow/import", {"file": f}, format="multipart"
            )
        self.assertEqual(import_response.status_code, 403)

        # DB state unchanged by any of the rejected writes.
        stage.refresh_from_db()
        self.assertTrue(stage.is_active)
        self.assertEqual(OperationStage.objects.count(), 2)
        self.assertEqual(OperationResult.objects.count(), 1)
        self.assertEqual(OperationTransition.objects.count(), 1)
        self.assertTrue(OperationTransition.objects.filter(id=transition.id).exists())

    # -- case 9: sale (contacts:view only) đọc được results VÀ stages (ô chọn cột
    # Tác nghiệp cần cả hai), nhưng không ghi được cái nào -------------------
    def test_contacts_view_only_user_can_read_catalogs_but_not_write(self):
        """Trước đây `stages` khoá riêng cho section contact_flow (final review C1).

        Mở đọc cho `contacts` là ĐỔI HÀNH VI CÓ CHỦ ĐÍCH: dropdown cột Tác nghiệp cần
        danh sách bước, mà sale thì không có section contact_flow. Quyền GHI không đổi.
        """
        sale = make_user_with_permissions("cf-sale-contacts", ["contacts:view"])
        self.client.force_authenticate(user=sale)

        self.assertEqual(self.client.get("/api/contact-flow/results").status_code, 200)
        self.assertEqual(self.client.get("/api/contact-flow/stages").status_code, 200)

        post_result = self.client.post(
            "/api/contact-flow/results", {"name": "KQ sale"}, format="json"
        )
        self.assertEqual(post_result.status_code, 403)
        post_stage = self.client.post(
            "/api/contact-flow/stages", {"name": "Bước sale"}, format="json"
        )
        self.assertEqual(post_stage.status_code, 403)
        self.assertFalse(OperationStage.objects.filter(name="Bước sale").exists())


# -- ràng buộc DB của các model được CRUD ở trên ------------------------


@pytest.mark.django_db
def test_stage_name_unique():
    OperationStage.objects.create(name="Gọi lần đầu", is_start=True)
    with pytest.raises(IntegrityError):
        OperationStage.objects.create(name="Gọi lần đầu")


@pytest.mark.django_db
def test_transition_unique_from_result():
    s1 = OperationStage.objects.create(name="A")
    s2 = OperationStage.objects.create(name="B")
    r = OperationResult.objects.create(name="KNM")
    OperationTransition.objects.create(from_stage=s1, result=r, to_stage=s2, delay_minutes=30)
    with pytest.raises(IntegrityError):
        OperationTransition.objects.create(from_stage=s1, result=r, to_stage=None)
