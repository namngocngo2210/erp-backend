from __future__ import annotations

import pytest
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import StockDocumentService
from apps.inventory.models import DocStatus, DocType, MovementType, StockDocument, StockItem, StockMovement
from apps.catalog.models import Product
from apps.users.models import User
from apps.users.tests.helpers import make_user_with_permissions
from apps.warehouses.models import Warehouse


class StockDocumentApprovalTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="W-APPR", name="Test Warehouse Approval")
        self.product = Product.objects.create(name="Test Product Approval", sku="TEST-APPR-1")
        self.user_a = User.objects.create_user(username="user-appr-a", password="x")
        self.user_b = User.objects.create_user(username="user-appr-b", password="x")

    def _create_receipt_draft(self, qty=10):
        return StockDocumentService.create_document(
            doc_type=DocType.RECEIPT,
            dest_warehouse_id=self.warehouse.id,
            lines=[{"product_id": self.product.id, "quantity": qty}],
            user_id=self.user_a.id,
        )

    # ── Task 1 smoke test (keeps existing passing test) ──────────────────────
    def test_doc_has_approval_fields_and_pending_state(self):
        assert DocStatus.PENDING_APPROVAL == "pending_approval"
        doc = StockDocument.objects.create(
            code="NK-T1", doc_type=DocType.RECEIPT, dest_warehouse=self.warehouse
        )
        assert doc.submitted_by_id is None and doc.submitted_at is None
        assert doc.approved_by_id is None and doc.approved_at is None
        assert doc.rejected_reason == ""

    # ── submit ────────────────────────────────────────────────────────────────
    def test_submit_moves_to_pending_no_movement(self):
        doc = self._create_receipt_draft()
        result = StockDocumentService.submit_for_approval(
            document_id=doc.id, user_id=self.user_a.id
        )
        result.refresh_from_db()

        assert result.status == DocStatus.PENDING_APPROVAL
        assert result.submitted_by_id == self.user_a.id
        assert result.submitted_at is not None
        # No movements should be created on submit
        assert StockMovement.objects.filter(reference_type="document", reference_id=doc.id).count() == 0
        assert not StockItem.objects.filter(warehouse=self.warehouse, product=self.product).exists()

    # ── approve ───────────────────────────────────────────────────────────────
    def test_approve_posts_and_applies_movement(self):
        doc = self._create_receipt_draft(qty=15)
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.user_a.id)
        result = StockDocumentService.approve_document(
            document_id=doc.id, user_id=self.user_b.id
        )
        result.refresh_from_db()

        assert result.status == DocStatus.POSTED
        assert result.posted_at is not None
        assert result.approved_by_id == self.user_b.id
        assert result.approved_at is not None
        # Stock movement created
        assert StockMovement.objects.filter(reference_type="document", reference_id=doc.id).count() == 1
        # StockItem updated
        item = StockItem.objects.get(warehouse=self.warehouse, product=self.product)
        assert item.quantity == 15

    # ── approve wrong state ───────────────────────────────────────────────────
    def test_approve_requires_pending(self):
        doc = self._create_receipt_draft()
        # doc is still DRAFT, not PENDING_APPROVAL
        with pytest.raises(ValueError):
            StockDocumentService.approve_document(document_id=doc.id, user_id=self.user_b.id)

    # ── self-approval ─────────────────────────────────────────────────────────
    def test_submitter_can_approve_own_document(self):
        doc = self._create_receipt_draft()
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.user_a.id)
        result = StockDocumentService.approve_document(document_id=doc.id, user_id=self.user_a.id)
        result.refresh_from_db()
        assert result.status == DocStatus.POSTED
        assert result.approved_by_id == self.user_a.id

    # ── reject ────────────────────────────────────────────────────────────────
    def test_reject_returns_to_draft_with_reason(self):
        doc = self._create_receipt_draft()
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.user_a.id)
        result = StockDocumentService.reject_document(
            document_id=doc.id, user_id=self.user_b.id, reason="Sai số lượng"
        )
        result.refresh_from_db()

        assert result.status == DocStatus.DRAFT
        assert result.rejected_reason == "Sai số lượng"
        assert result.submitted_by_id is None
        assert result.submitted_at is None
        # No movements created
        assert StockMovement.objects.filter(reference_type="document", reference_id=doc.id).count() == 0


# ── API endpoint tests ────────────────────────────────────────────────────────

class StockDocumentApprovalApiTests(TestCase):
    """
    Test submit/approve/reject/cancel API endpoints with RBAC gating.

    Permission setup (contract sweep N2 đợt 2 + grandfather 0039):
    - staff_user:   stock-documents:view + :edit (role kho prod sau grandfather —
                    submit là hành vi GHI, gate :edit; :approve mới gác duyệt)
    - view_user:    stock-documents:view ONLY (trạng thái sau khi admin thu hồi
                    :edit trên UI — không submit được nữa)
    - approve_user: stock-documents:approve (duyệt/từ chối/huỷ)
    """

    def setUp(self):
        self.client = APIClient()
        self.warehouse = Warehouse.objects.create(code="W-API-APPR", name="API Approval WH")
        self.product = Product.objects.create(name="API Appr Product", sku="API-APPR-1")

        # NOTE: make_user_with_sections grants ALL actions in a section, so we use
        # make_user_with_permissions directly to grant specific codes.
        # Role kho vận hành sau grandfather 0039: :view kèm :edit (submit được,
        # không approve/cancel được).
        self.staff_user = make_user_with_permissions(
            "appr-staff", ["stock-documents:view", "stock-documents:edit"]
        )
        # Chỉ còn :view (admin đã thu hồi :edit) — không submit được.
        self.view_user = make_user_with_permissions("appr-view", ["stock-documents:view"])
        # User with :approve on stock-documents (can approve/reject/cancel)
        self.approve_user = make_user_with_permissions("appr-approver", ["stock-documents:approve"])

    def _create_draft_via_service(self, user=None, qty=10):
        """Create a draft document directly via service (bypasses API auth)."""
        uid = (user or self.view_user).id
        return StockDocumentService.create_document(
            doc_type=DocType.RECEIPT,
            dest_warehouse_id=self.warehouse.id,
            lines=[{"product_id": self.product.id, "quantity": qty}],
            user_id=uid,
        )

    # ── submit ────────────────────────────────────────────────────────────────

    def test_grandfathered_editor_can_submit(self):
        """Contract mới: submit gate bằng stock-documents:edit (không còn :view trần).
        Role kho prod có :view nhận :edit qua grandfather 0039 → vẫn submit được."""
        doc = self._create_draft_via_service()
        self.client.force_authenticate(self.staff_user)
        resp = self.client.post(reverse("inventory-document-submit", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 200, resp.json())
        self.assertEqual(resp.json()["status"], "pending_approval")

    def test_view_only_user_cannot_submit(self):
        """Sau khi thu hồi :edit trên UI, :view trần không submit được nữa —
        đây chính là đòn bẩy siết thật của chiến lược grandfather."""
        doc = self._create_draft_via_service()
        self.client.force_authenticate(self.view_user)
        resp = self.client.post(reverse("inventory-document-submit", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 403)

    def test_submit_wrong_state_returns_400(self):
        doc = self._create_draft_via_service()
        # Already submit once
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.staff_user.id)
        # Try to submit again (wrong state)
        self.client.force_authenticate(self.staff_user)
        resp = self.client.post(reverse("inventory-document-submit", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_submit_returns_401(self):
        doc = self._create_draft_via_service()
        resp = self.client.post(reverse("inventory-document-submit", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 401)

    # ── approve: view_user cannot ─────────────────────────────────────────────

    def test_view_user_cannot_approve_or_reject(self):
        for action in ("approve", "reject"):
            with self.subTest(action=action):
                doc = self._create_draft_via_service()
                StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.view_user.id)
                self.client.force_authenticate(self.view_user)
                resp = self.client.post(
                    reverse(f"inventory-document-{action}", kwargs={"document_id": doc.id})
                )
                self.assertEqual(resp.status_code, 403)

    # ── approve: approve_user can ─────────────────────────────────────────────

    def test_approve_user_can_approve_pending_document(self):
        doc = self._create_draft_via_service(user=self.view_user)
        # submit by view_user
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.view_user.id)
        # approve by approve_user
        self.client.force_authenticate(self.approve_user)
        resp = self.client.post(reverse("inventory-document-approve", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 200, resp.json())
        body = resp.json()
        self.assertEqual(body["status"], "posted")
        self.assertIsNotNone(body["approved_by"])
        self.assertIsNotNone(body["approved_at"])
        # serializer should include approved_by_name
        self.assertEqual(body["approved_by_name"], self.approve_user.username)

    def test_self_approval_is_allowed(self):
        """approve_user submits then approves own doc → 200 (self-approval allowed)."""
        doc = self._create_draft_via_service(user=self.approve_user)
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.approve_user.id)
        self.client.force_authenticate(self.approve_user)
        resp = self.client.post(reverse("inventory-document-approve", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 200, resp.json())
        self.assertEqual(resp.json()["status"], "posted")

    # ── reject ────────────────────────────────────────────────────────────────

    def test_approve_user_can_reject(self):
        doc = self._create_draft_via_service()
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.view_user.id)
        self.client.force_authenticate(self.approve_user)
        resp = self.client.post(
            reverse("inventory-document-reject", kwargs={"document_id": doc.id}),
            data={"reason": "Sai số lượng"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.json())
        body = resp.json()
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["rejected_reason"], "Sai số lượng")

    # ── cancel ────────────────────────────────────────────────────────────────

    def test_view_user_cannot_cancel(self):
        doc = self._create_draft_via_service()
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.view_user.id)
        StockDocumentService.approve_document(document_id=doc.id, user_id=self.approve_user.id)
        self.client.force_authenticate(self.view_user)
        resp = self.client.post(reverse("inventory-document-cancel", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 403)

    def test_approve_user_can_cancel(self):
        doc = self._create_draft_via_service(user=self.view_user)
        StockDocumentService.submit_for_approval(document_id=doc.id, user_id=self.view_user.id)
        StockDocumentService.approve_document(document_id=doc.id, user_id=self.approve_user.id)
        self.client.force_authenticate(self.approve_user)
        resp = self.client.post(reverse("inventory-document-cancel", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 200, resp.json())
        self.assertEqual(resp.json()["status"], "cancelled")

    # ── serializer fields ─────────────────────────────────────────────────────

    def test_submit_response_includes_approval_fields(self):
        doc = self._create_draft_via_service()
        self.client.force_authenticate(self.staff_user)
        resp = self.client.post(reverse("inventory-document-submit", kwargs={"document_id": doc.id}))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # All new approval fields must be present
        for field in ("submitted_by", "submitted_by_name", "submitted_at",
                      "approved_by", "approved_by_name", "approved_at", "rejected_reason"):
            self.assertIn(field, body, f"Missing field: {field}")
        self.assertEqual(body["submitted_by"], self.staff_user.id)
        self.assertEqual(body["submitted_by_name"], self.staff_user.username)


# ---------------------------------------------------------------------------
# Duyệt phiếu chuyển kho phải ghi sổ theo thứ tự toàn cục (warehouse, product).
#
# Phiếu TRANSFER có 2 kho — khoá src→dst theo từng dòng nghĩa là phiếu W1→W2 và
# phiếu W2→W1 cùng sản phẩm duyệt đồng thời sẽ giữ-chéo row `stock_items`
# (AB/BA) → deadlock, Postgres abort 1 transaction → duyệt phiếu fail 500 ngẫu
# nhiên. `cancel_document` đã sort toàn cục; approve phải cùng luật.
# ---------------------------------------------------------------------------


@pytest.fixture
def warehouses():
    # Tạo dest TRƯỚC để dest.id < src.id — thứ tự "src rồi dest" của từng dòng
    # khi đó KHÁC thứ tự toàn cục, test mới phân biệt được hai cách khoá.
    dest = Warehouse.objects.create(code="WH-B", name="Kho B")
    src = Warehouse.objects.create(code="WH-A", name="Kho A")
    return src, dest


@pytest.fixture
def products():
    p1 = Product.objects.create(name="SP 1", sku="LOCK-1")
    p2 = Product.objects.create(name="SP 2", sku="LOCK-2")
    return p1, p2


@pytest.mark.django_db
def test_transfer_approve_locks_in_global_warehouse_product_order(
    monkeypatch, warehouses, products
):
    src, dest = warehouses
    p1, p2 = products
    for product in (p1, p2):
        StockCommandService.record_movement(
            warehouse_id=src.id, product_id=product.id, qty_delta=5,
            movement_type=MovementType.RECEIPT,
        )
    doc = StockDocumentService.create_document(
        doc_type=DocType.TRANSFER,
        source_warehouse_id=src.id,
        dest_warehouse_id=dest.id,
        lines=[
            {"product_id": p1.id, "quantity": 1},
            {"product_id": p2.id, "quantity": 1},
        ],
    )
    StockDocumentService.submit_for_approval(document_id=doc.id)

    emitted: list[tuple[int, int]] = []
    real_record = StockCommandService.record_movement

    def spy(**kwargs):
        emitted.append((kwargs["warehouse_id"], kwargs["product_id"]))
        return real_record(**kwargs)

    monkeypatch.setattr(StockCommandService, "record_movement", staticmethod(spy))

    StockDocumentService.approve_document(document_id=doc.id)

    assert emitted == sorted(emitted), (
        "bút toán phải đi theo thứ tự toàn cục (warehouse_id, product_id) "
        f"để không deadlock với phiếu ngược chiều; thực tế: {emitted}"
    )
    # Kết quả tồn không đổi so với luật cũ.
    assert StockItem.objects.get(
        warehouse=src, product=p1, state="sellable"
    ).quantity == 4
    assert StockItem.objects.get(
        warehouse=dest, product=p1, state="sellable"
    ).quantity == 1
