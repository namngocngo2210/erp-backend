"""Xuất Excel cho TỪNG phiếu kho (`GET /api/inventory/documents/<id>/export`).

File 1 phiếu = khối đầu phiếu + bảng dòng hàng: người kho in ra để đối chiếu,
nên tên file phải mang mã phiếu và nội dung phải khớp phiếu trên màn hình.
"""
from __future__ import annotations

from io import BytesIO

from django.test import TestCase
from openpyxl import load_workbook
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.users.tests.helpers import make_admin, make_user_with_permissions
from apps.warehouses.models import Warehouse

_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class StockDocumentExportApiTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_docexport")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WX", name="Kho Xuất")
        self.product = Product.objects.create(name="Sản phẩm A", sku="SKU-A")
        self.other = Product.objects.create(name="Sản phẩm B", sku="SKU-B")
        self.doc = StockDocumentService.create_document(
            doc_type="receipt",
            lines=[
                {"product_id": self.product.id, "quantity": 3},
                {"product_id": self.other.id, "quantity": 4},
            ],
            dest_warehouse_id=self.wh.id,
            note="Ghi chú thử",
            user_id=self.user.id,
        )

    def _get(self, client=None):
        return (client or self.client).get(
            f"/api/inventory/documents/{self.doc.id}/export"
        )

    def _sheet_cells(self, resp):
        ws = load_workbook(BytesIO(resp.content)).active
        return [
            [c if c is not None else "" for c in row]
            for row in ws.iter_rows(values_only=True)
        ]

    def test_export_returns_xlsx_named_after_document_code(self):
        resp = self._get()
        assert resp.status_code == 200, resp.content[:200]
        assert _XLSX_CONTENT_TYPE in resp.get("Content-Type", "")
        disposition = resp.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert self.doc.code in disposition

    def test_export_contains_document_header_fields(self):
        flat = [str(v) for row in self._sheet_cells(self._get()) for v in row]
        assert self.doc.code in flat
        assert "Nhập kho" in flat
        assert "Nháp" in flat
        assert "Kho Xuất" in flat
        assert "Ghi chú thử" in flat

    def test_export_contains_line_table_and_total(self):
        rows = self._sheet_cells(self._get())
        header_idx = next(
            i for i, r in enumerate(rows) if list(r)[:4] == ["STT", "SKU", "Tên sản phẩm", "Số lượng"]
        )
        line_rows = rows[header_idx + 1 : header_idx + 3]
        assert [r[1] for r in line_rows] == ["SKU-A", "SKU-B"]
        assert [r[2] for r in line_rows] == ["Sản phẩm A", "Sản phẩm B"]
        assert [r[3] for r in line_rows] == [3, 4]
        total_row = rows[header_idx + 3]
        assert "Tổng" in str(total_row[0]) or "Tổng" in str(total_row[2])
        assert total_row[3] == 7

    def test_export_unknown_document_returns_404(self):
        resp = self.client.get("/api/inventory/documents/999999/export")
        assert resp.status_code == 404

    def test_export_requires_stock_documents_view_permission(self):
        stranger = make_user_with_permissions("no_view_docexport", ["orders:view"])
        client = APIClient()
        client.force_authenticate(stranger)
        assert self._get(client).status_code == 403
