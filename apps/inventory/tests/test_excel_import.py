from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.inventory.infrastructure.excel_lines import (
    build_lines_import_template,
    parse_lines_from_workbook,
)
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin

_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _build_workbook_bytes(rows: list[list]) -> bytes:
    """Helper: build an in-memory xlsx with a fixed header row + given data rows."""
    wb = Workbook()
    ws = wb.active
    ws.append(["SKU", "Số lượng", "Ghi chú"])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _make_upload(rows: list[list]) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        "test.xlsx",
        _build_workbook_bytes(rows),
        content_type=_XLSX_CONTENT_TYPE,
    )


class ParseLinesFromWorkbookTests(TestCase):
    def setUp(self):
        self.product_a = Product.objects.create(name="Sản phẩm A", sku="SKU-A")
        self.product_b = Product.objects.create(name="Sản phẩm B", sku="SKU-B")

    def test_valid_rows_return_correct_lines(self):
        upload = _make_upload([
            ["SKU-A", 5, "ghi chú A"],
            ["SKU-B", 3, ""],
        ])
        result = parse_lines_from_workbook(upload)

        assert len(result["lines"]) == 2
        assert result["errors"] == []

        line_a = next(ln for ln in result["lines"] if ln["sku"] == "SKU-A")
        assert line_a["product_id"] == self.product_a.id
        assert line_a["name"] == "Sản phẩm A"
        assert line_a["quantity"] == 5
        assert line_a["condition_note"] == "ghi chú A"

        line_b = next(ln for ln in result["lines"] if ln["sku"] == "SKU-B")
        assert line_b["quantity"] == 3

    def test_unknown_sku_becomes_error(self):
        upload = _make_upload([
            ["SKU-A", 2, ""],
            ["SKU-UNKNOWN", 1, ""],
        ])
        result = parse_lines_from_workbook(upload)

        assert len(result["lines"]) == 1
        assert len(result["errors"]) == 1
        err = result["errors"][0]
        assert err["sku"] == "SKU-UNKNOWN"
        assert "tồn tại" in err["message"]
        assert err["row"] == 3  # header is row 1, first data row is 2

    def test_duplicate_sku_is_merged_with_summed_quantity(self):
        upload = _make_upload([
            ["SKU-A", 4, "note first"],
            ["SKU-B", 2, ""],
            ["SKU-A", 6, "note second"],  # duplicate — should be merged
        ])
        result = parse_lines_from_workbook(upload)

        # Only 2 unique products
        assert len(result["lines"]) == 2
        assert result["errors"] == []

        line_a = next(ln for ln in result["lines"] if ln["sku"] == "SKU-A")
        assert line_a["quantity"] == 10  # 4 + 6 summed
        assert line_a["condition_note"] == "note first"  # keeps first note

    def test_empty_row_is_skipped(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["SKU", "Số lượng", "Ghi chú"])
        ws.append(["SKU-A", 1, ""])
        ws.append([None, None, None])  # fully empty row
        ws.append(["SKU-B", 2, ""])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        upload = SimpleUploadedFile("t.xlsx", buf.getvalue(), content_type=_XLSX_CONTENT_TYPE)

        result = parse_lines_from_workbook(upload)
        assert len(result["lines"]) == 2
        assert result["errors"] == []

    def test_missing_sku_column_raises_value_error(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Mã hàng", "Số lượng"])  # no SKU column
        ws.append(["X", 1])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        upload = SimpleUploadedFile("t.xlsx", buf.getvalue(), content_type=_XLSX_CONTENT_TYPE)

        with self.assertRaises(ValueError) as ctx:
            parse_lines_from_workbook(upload)
        assert "SKU" in str(ctx.exception)

    def test_invalid_quantity_becomes_error(self):
        upload = _make_upload([
            ["SKU-A", "không phải số", ""],
        ])
        result = parse_lines_from_workbook(upload)

        assert result["lines"] == []
        assert len(result["errors"]) == 1
        assert result["errors"][0]["sku"] == "SKU-A"

    def test_zero_quantity_becomes_error(self):
        upload = _make_upload([
            ["SKU-A", 0, ""],
        ])
        result = parse_lines_from_workbook(upload)

        assert result["lines"] == []
        assert len(result["errors"]) == 1

    def test_alias_headers_are_accepted(self):
        """Column headers using Vietnamese aliases should be parsed correctly."""
        wb = Workbook()
        ws = wb.active
        ws.append(["Mã SP", "SL", "Ghi chu"])
        ws.append(["SKU-A", 7, "ok"])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        upload = SimpleUploadedFile("t.xlsx", buf.getvalue(), content_type=_XLSX_CONTENT_TYPE)

        result = parse_lines_from_workbook(upload)
        assert len(result["lines"]) == 1
        assert result["lines"][0]["quantity"] == 7

    def test_bytesio_input_works(self):
        """parse_lines_from_workbook should also accept a plain BytesIO."""
        buf = BytesIO(_build_workbook_bytes([["SKU-A", 3, ""]]))
        buf.seek(0)
        result = parse_lines_from_workbook(buf)
        assert len(result["lines"]) == 1

    def test_combined_scenario(self):
        """Full scenario: 2 valid + 1 unknown + 1 duplicate → 2 lines, 1 error."""
        upload = _make_upload([
            ["SKU-A", 5, "note"],
            ["SKU-B", 3, ""],
            ["SKU-GHOST", 1, ""],
            ["SKU-A", 2, ""],  # duplicate
        ])
        result = parse_lines_from_workbook(upload)

        assert len(result["lines"]) == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["sku"] == "SKU-GHOST"

        line_a = next(ln for ln in result["lines"] if ln["sku"] == "SKU-A")
        assert line_a["quantity"] == 7  # 5 + 2


class BuildLinesImportTemplateTests(SimpleTestCase):
    def test_returns_non_empty_bytes(self):
        data = build_lines_import_template()
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_template_is_valid_xlsx(self):
        from openpyxl import load_workbook

        data = build_lines_import_template()
        wb = load_workbook(BytesIO(data))
        ws = wb.active
        headers = [ws.cell(1, col).value for col in range(1, 4)]
        assert headers == ["SKU", "Số lượng", "Ghi chú"]


class DownloadTemplateApiTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_tmpl")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_template_endpoint_returns_200_with_xlsx_content_type(self):
        resp = self.client.get("/api/inventory/import/template")
        assert resp.status_code == 200, resp.content[:200]
        assert _XLSX_CONTENT_TYPE in resp.get("Content-Type", "")

    def test_template_endpoint_has_attachment_disposition(self):
        resp = self.client.get("/api/inventory/import/template")
        assert resp.status_code == 200
        disposition = resp.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert "inventory-import-template.xlsx" in disposition


class ParseImportLinesApiTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_parse")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.product = Product.objects.create(name="Sản phẩm Test", sku="SKU-TEST")

    def test_missing_file_returns_400(self):
        resp = self.client.post("/api/inventory/import/parse-lines", data={}, format="multipart")
        assert resp.status_code == 400
        assert "file" in resp.json().get("detail", "").lower() or "thiếu" in resp.json().get("detail", "").lower()

    def test_valid_upload_returns_200_with_lines(self):
        upload = _make_upload([["SKU-TEST", 3, "ok"]])
        resp = self.client.post(
            "/api/inventory/import/parse-lines",
            data={"file": upload},
            format="multipart",
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert "lines" in body
        assert "errors" in body
        assert len(body["lines"]) == 1
        assert body["lines"][0]["sku"] == "SKU-TEST"
        assert body["lines"][0]["quantity"] == 3

    def test_unauthenticated_returns_403(self):
        anon_client = APIClient()
        upload = _make_upload([["SKU-TEST", 1, ""]])
        resp = anon_client.post(
            "/api/inventory/import/parse-lines",
            data={"file": upload},
            format="multipart",
        )
        assert resp.status_code in (401, 403)
