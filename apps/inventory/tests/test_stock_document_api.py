"""API phiếu kho: CRUD + submit/approve/cancel, các fix hardening và sổ phiếu kho.

Gộp từ test_document_api.py, test_document_hardening.py và
test_ledger_documents.py (tái cấu trúc test 2026-08-05).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import DocStatus, DocType, ReturnType
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin, make_user_with_permissions
from apps.warehouses.models import Warehouse


class StockDocumentApiTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="W1", name="Kho 1")
        self.product = Product.objects.create(name="SP A", sku="A-1")
        # Separate approver so admin (submitter) does not self-approve
        self.approver = make_user_with_permissions("approver1", ["stock-documents:approve"])
        self.approver_client = APIClient()
        self.approver_client.force_authenticate(self.approver)

    def _create_receipt(self, qty=5):
        return self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "receipt",
                "dest_warehouse_id": self.wh.id,
                "lines": [{"product_id": self.product.id, "quantity": qty}],
            },
            format="json",
        )

    def _submit_and_approve(self, doc_id):
        """Submit by admin then approve by approver."""
        submit_resp = self.client.post(f"/api/inventory/documents/{doc_id}/submit", format="json")
        assert submit_resp.status_code == 200, submit_resp.json()
        approve_resp = self.approver_client.post(f"/api/inventory/documents/{doc_id}/approve", format="json")
        assert approve_resp.status_code == 200, approve_resp.json()
        return approve_resp

    def test_create_receipt_returns_201_draft(self):
        resp = self._create_receipt()
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["status"] == "draft"
        assert body["doc_type"] == "receipt"
        assert body["code"].startswith("NK")
        assert len(body["lines"]) == 1
        assert body["lines"][0]["quantity"] == 5

    def test_create_document_rejects_future_date_with_400(self):
        """Ngày chứng từ = ngày nghiệp vụ của bút toán, mà bút toán cấm ngày tương
        lai → phải chặn ngay lúc tạo bằng 400, không để lỗi rơi thành 500 (hoặc tệ
        hơn: phiếu tạo xong rồi kẹt vĩnh viễn vì lần duyệt nào cũng nổ)."""
        resp = self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "receipt",
                "dest_warehouse_id": self.wh.id,
                "created_at": (timezone.now() + timedelta(days=1)).isoformat(),
                "lines": [{"product_id": self.product.id, "quantity": 1}],
            },
            format="json",
        )
        assert resp.status_code == 400, resp.status_code

    def test_submit_and_approve_document_increases_stock(self):
        # Create a receipt draft
        resp = self._create_receipt(qty=10)
        assert resp.status_code == 201
        doc_id = resp.json()["id"]

        # Submit then approve the document
        approve_resp = self._submit_and_approve(doc_id)
        assert approve_resp.json()["status"] == "posted"

        # Verify stock increased
        stock_resp = self.client.get("/api/inventory/stock", {"limit": 50, "offset": 0})
        assert stock_resp.status_code == 200
        body = stock_resp.json()
        assert body["total"] == 1
        assert body["items"][0]["quantity"] == 10
        assert body["items"][0]["product_sku"] == "A-1"

    def test_cancel_document_reverts_stock(self):
        # Create and post a receipt via submit+approve
        resp = self._create_receipt(qty=5)
        assert resp.status_code == 201
        doc_id = resp.json()["id"]

        self._submit_and_approve(doc_id)

        # Cancel the document (requires :approve permission — approver_client)
        cancel_resp = self.approver_client.post(f"/api/inventory/documents/{doc_id}/cancel", format="json")
        assert cancel_resp.status_code == 200, cancel_resp.json()
        assert cancel_resp.json()["status"] == "cancelled"

        # Verify stock is back to 0
        stock_resp = self.client.get("/api/inventory/stock", {"limit": 50, "offset": 0})
        assert stock_resp.status_code == 200
        items = stock_resp.json()["items"]
        # Stock item should exist but quantity should be 0
        matching = [i for i in items if i["product_sku"] == "A-1"]
        assert len(matching) == 1
        assert matching[0]["quantity"] == 0

    def test_create_issue_without_source_warehouse_returns_400(self):
        resp = self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "issue",
                "lines": [{"product_id": self.product.id, "quantity": 3}],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_get_document_detail(self):
        resp = self._create_receipt()
        assert resp.status_code == 201
        doc_id = resp.json()["id"]

        detail_resp = self.client.get(f"/api/inventory/documents/{doc_id}")
        assert detail_resp.status_code == 200
        body = detail_resp.json()
        assert body["id"] == doc_id
        assert body["status"] == "draft"

    def test_get_document_detail_not_found(self):
        resp = self.client.get("/api/inventory/documents/99999")
        assert resp.status_code == 404

    def test_list_documents_paginated(self):
        self._create_receipt(qty=5)
        self._create_receipt(qty=10)

        resp = self.client.get("/api/inventory/documents", {"limit": 50, "offset": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert "limit" in body
        assert "offset" in body

    def test_submit_already_pending_document_returns_400(self):
        resp = self._create_receipt()
        assert resp.status_code == 201
        doc_id = resp.json()["id"]

        self.client.post(f"/api/inventory/documents/{doc_id}/submit", format="json")
        # Submit again — should fail (already pending_approval)
        resp2 = self.client.post(f"/api/inventory/documents/{doc_id}/submit", format="json")
        assert resp2.status_code == 400

    def test_cancel_draft_document_returns_400(self):
        resp = self._create_receipt()
        assert resp.status_code == 201
        doc_id = resp.json()["id"]

        cancel_resp = self.approver_client.post(f"/api/inventory/documents/{doc_id}/cancel", format="json")
        assert cancel_resp.status_code == 400

    # --- Transfer API tests ---

    def test_create_transfer_missing_source_returns_400(self):
        wh2 = Warehouse.objects.create(code="W2", name="Kho 2")
        resp = self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "transfer",
                "dest_warehouse_id": wh2.id,
                "lines": [{"product_id": self.product.id, "quantity": 2}],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_create_transfer_missing_dest_returns_400(self):
        resp = self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "transfer",
                "source_warehouse_id": self.wh.id,
                "lines": [{"product_id": self.product.id, "quantity": 2}],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_create_valid_transfer_returns_201_and_moves_stock(self):
        wh2 = Warehouse.objects.create(code="W2", name="Kho 2")

        # seed source warehouse with stock via receipt (submit+approve)
        recv_resp = self._create_receipt(qty=10)
        assert recv_resp.status_code == 201
        recv_id = recv_resp.json()["id"]
        self._submit_and_approve(recv_id)

        # create transfer
        resp = self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "transfer",
                "source_warehouse_id": self.wh.id,
                "dest_warehouse_id": wh2.id,
                "lines": [{"product_id": self.product.id, "quantity": 4}],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["doc_type"] == "transfer"
        assert body["status"] == "draft"
        assert body["code"].startswith("CK")

        # submit + approve the transfer
        doc_id = body["id"]
        self._submit_and_approve(doc_id)

        # verify stock levels
        stock_resp = self.client.get("/api/inventory/stock", {"limit": 50, "offset": 0})
        assert stock_resp.status_code == 200
        items = stock_resp.json()["items"]
        src_items = [i for i in items if i["warehouse"] == self.wh.id and i["product_sku"] == "A-1"]
        dst_items = [i for i in items if i["warehouse"] == wh2.id and i["product_sku"] == "A-1"]
        assert src_items[0]["quantity"] == 6
        assert dst_items[0]["quantity"] == 4

    def test_create_transfer_same_warehouse_returns_400(self):
        resp = self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "transfer",
                "source_warehouse_id": self.wh.id,
                "dest_warehouse_id": self.wh.id,
                "lines": [{"product_id": self.product.id, "quantity": 2}],
            },
            format="json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests cho các fix hardening trên phiếu kho / phiếu trả hàng:
#
# - serializer chặn trùng sản phẩm trong lines (400, không còn IntegrityError 500);
# - rejected_reason được xoá khi gửi duyệt lại;
# - huỷ phiếu trả posted cần returns:approve;
# - huỷ phiếu kho posted hoàn đúng state của movement gốc.
# ---------------------------------------------------------------------------


class DuplicateLineValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("admin-dup"))
        self.wh = Warehouse.objects.create(code="WH-DUP", name="Kho Dup")
        self.product = Product.objects.create(name="SP Dup", sku="SKU-DUP")

    def test_duplicate_product_in_document_lines_returns_400(self):
        resp = self.client.post(
            "/api/inventory/documents",
            data={
                "doc_type": "receipt",
                "dest_warehouse_id": self.wh.id,
                "lines": [
                    {"product_id": self.product.id, "quantity": 1},
                    {"product_id": self.product.id, "quantity": 2},
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_product_in_return_lines_returns_400(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 1,
                        "return_type": "warranty",
                    },
                    {
                        "product_id": self.product.id,
                        "quantity": 2,
                        "return_type": "warranty",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


class RejectedReasonResetTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="WH-RJ", name="Kho Reject")
        self.product = Product.objects.create(name="SP Reject", sku="SKU-RJ")

    def test_resubmit_clears_rejected_reason(self):
        doc = StockDocumentService.create_document(
            doc_type=DocType.RECEIPT,
            lines=[{"product_id": self.product.id, "quantity": 1}],
            dest_warehouse_id=self.wh.id,
        )
        StockDocumentService.submit_for_approval(document_id=doc.id)
        StockDocumentService.reject_document(document_id=doc.id, reason="thiếu chứng từ")
        doc.refresh_from_db()
        self.assertEqual(doc.rejected_reason, "thiếu chứng từ")
        self.assertEqual(doc.status, DocStatus.DRAFT)

        StockDocumentService.submit_for_approval(document_id=doc.id)
        doc.refresh_from_db()
        self.assertEqual(doc.rejected_reason, "", "Gửi duyệt lại phải xoá lý do từ chối cũ")


class CancelReturnRbacTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.wh = Warehouse.objects.create(code="WH-CRB", name="Kho Cancel RBAC")
        self.product = Product.objects.create(name="SP CRB", sku="SKU-CRB")

    def _create_and_post_return(self, client) -> int:
        resp = client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "auto_post": True,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "quantity": 1,
                        "return_type": "warranty",
                    }
                ],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        assert resp.json()["status"] == "posted"
        return resp.json()["id"]

    def test_cancel_posted_return_requires_approve(self):
        """User chỉ có returns:view/create/edit (không approve) → 403 khi huỷ."""
        staff = make_user_with_permissions(
            "returns-staff-crb", ["returns:view", "returns:create", "returns:edit"]
        )
        self.client.force_authenticate(staff)
        rid = self._create_and_post_return(self.client)

        cancel = self.client.post(f"/api/inventory/returns/{rid}/cancel", format="json")
        self.assertEqual(cancel.status_code, 403)

    def test_cancel_posted_return_ok_with_admin(self):
        self.client.force_authenticate(make_admin("admin-crb"))
        rid = self._create_and_post_return(self.client)
        cancel = self.client.post(f"/api/inventory/returns/{rid}/cancel", format="json")
        self.assertEqual(cancel.status_code, 200)


class CancelDocumentStateTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="WH-CST", name="Kho Cancel State")
        self.product = Product.objects.create(name="SP CST", sku="SKU-CST")

    def test_cancel_receipt_reverses_with_same_state(self):
        doc = StockDocumentService.create_document(
            doc_type=DocType.RECEIPT,
            lines=[{"product_id": self.product.id, "quantity": 4}],
            dest_warehouse_id=self.wh.id,
        )
        StockDocumentService.submit_for_approval(document_id=doc.id)
        StockDocumentService.approve_document(document_id=doc.id)
        StockDocumentService.cancel_document(document_id=doc.id)

        from apps.inventory.models import StockItem

        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state="sellable"
        )
        self.assertEqual(item.quantity, 0, "Huỷ phiếu nhập phải trả tồn về 0")


# ---------------------------------------------------------------------------
# Sổ phiếu kho (`/api/inventory/ledger-documents`) — phiếu trả đã rời sổ này
# (gỡ UI 2026-07-14): đơn hoàn theo dõi ở tab Nhập hoàn của Lịch sử nhập xuất.
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger_api(db):
    user = make_admin("admin_ledger_docs")
    client = APIClient()
    client.force_authenticate(user=user)
    wh = Warehouse.objects.create(name="Kho Sổ", code="LDG")
    product = Product.objects.create(sku="LDG-1", name="SP Sổ")
    return client, wh, product


def _receipt(wh, product, qty=1):
    return StockDocumentService.create_document(
        doc_type=DocType.RECEIPT,
        lines=[{"product_id": product.id, "quantity": qty}],
        dest_warehouse_id=wh.id,
    )


@pytest.mark.django_db
def test_ledger_lists_stock_documents(ledger_api):
    client, wh, p = ledger_api
    doc = _receipt(wh, p, qty=2)

    res = client.get("/api/inventory/ledger-documents", {"limit": 50, "offset": 0})
    assert res.status_code == 200, res.data
    assert res.data["total"] == 1
    row = res.data["items"][0]
    assert row["id"] == doc.id
    assert row["code"] == doc.code
    assert row["type"] == "receipt"
    assert row["status"] == "draft"
    assert row["warehouse_label"] == "Kho Sổ"
    assert row["has_attachments"] is False


@pytest.mark.django_db
def test_ledger_filters_by_type_status_and_code(ledger_api):
    client, wh, p = ledger_api
    receipt = _receipt(wh, p)
    issue = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        lines=[{"product_id": p.id, "quantity": 1}],
        source_warehouse_id=wh.id,
    )

    by_type = client.get("/api/inventory/ledger-documents", {"type": "receipt"})
    assert [r["id"] for r in by_type.data["items"]] == [receipt.id]

    by_status = client.get("/api/inventory/ledger-documents", {"status": "draft"})
    assert by_status.data["total"] == 2

    by_code = client.get("/api/inventory/ledger-documents", {"q": issue.code})
    assert [r["id"] for r in by_code.data["items"]] == [issue.id]

    miss = client.get("/api/inventory/ledger-documents", {"q": "KHONG-TON-TAI"})
    assert miss.data["items"] == []


@pytest.mark.django_db
def test_ledger_does_not_list_return_notes(ledger_api):
    """Phiếu ngày trả hàng không còn xuất hiện trong sổ phiếu kho."""
    client, wh, p = ledger_api
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        shipping_code="SPX1",
        lines=[{
            "product_id": p.id,
            "quantity": 2,
            "return_type": ReturnType.DELIVERY_RETURNED,
        }],
    )

    res = client.get("/api/inventory/ledger-documents", {"limit": 50, "offset": 0})
    assert res.status_code == 200, res.data
    assert res.data["total"] == 0
    assert res.data["items"] == []

    # Lọc type=return (client cũ) không lỗi, chỉ trả rỗng.
    legacy = client.get("/api/inventory/ledger-documents", {"type": "return"})
    assert legacy.status_code == 200
    assert legacy.data["items"] == []
