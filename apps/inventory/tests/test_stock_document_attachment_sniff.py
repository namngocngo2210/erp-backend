from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.inventory.application.services.stock_document_attachment_service import (
    AttachmentValidationError,
    StockDocumentAttachmentService,
)
from apps.inventory.models import DocType, StockDocument
from apps.warehouses.models import Warehouse

_REAL_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 10


class StockDocumentAttachmentSniffTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="W-SNIFF", name="Test Warehouse Sniff")
        self.doc = StockDocument.objects.create(
            code="NK-SNIFF-1", doc_type=DocType.RECEIPT, dest_warehouse=self.warehouse
        )

    @patch(
        "apps.inventory.application.services.stock_document_attachment_service.document_storage"
    )
    def test_garbage_bytes_claiming_png_is_rejected(self, mock_storage):
        with self.assertRaises(AttachmentValidationError):
            StockDocumentAttachmentService.add_attachment(
                document_id=self.doc.id,
                data=b"\x00\x01garbage",
                content_type="image/png",
            )
        mock_storage.put_object.assert_not_called()

    @patch(
        "apps.inventory.application.services.stock_document_attachment_service.document_storage"
    )
    def test_real_png_with_wrong_claimed_type_is_accepted_and_stored_as_png(
        self, mock_storage
    ):
        att = StockDocumentAttachmentService.add_attachment(
            document_id=self.doc.id,
            data=_REAL_PNG,
            content_type="application/pdf",
        )

        assert att.content_type == "image/png"
        assert att.file_key.endswith(".png")
        mock_storage.put_object.assert_called_once()
        _, put_data, put_content_type = mock_storage.put_object.call_args[0]
        assert put_data == _REAL_PNG
        assert put_content_type == "image/png"

    @patch(
        "apps.inventory.application.services.stock_document_attachment_service.document_storage"
    )
    def test_valid_webp_is_accepted_and_stored_as_webp(self, mock_storage):
        webp = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"0" * 12

        att = StockDocumentAttachmentService.add_attachment(
            document_id=self.doc.id,
            data=webp,
            content_type="application/octet-stream",
        )

        assert att.content_type == "image/webp"
        assert att.file_key.endswith(".webp")
        mock_storage.put_object.assert_called_once()
        _, put_data, put_content_type = mock_storage.put_object.call_args[0]
        assert put_data == webp
        assert put_content_type == "image/webp"

    @patch(
        "apps.inventory.application.services.stock_document_attachment_service.document_storage"
    )
    def test_riff_but_not_webp_is_rejected(self, mock_storage):
        wav = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVE" + b"0" * 12

        with self.assertRaises(AttachmentValidationError):
            StockDocumentAttachmentService.add_attachment(
                document_id=self.doc.id,
                data=wav,
                content_type="image/webp",
            )
        mock_storage.put_object.assert_not_called()

    @patch(
        "apps.inventory.application.services.stock_document_attachment_service.document_storage"
    )
    def test_unrecognized_bytes_rejected_even_with_no_claimed_type(self, mock_storage):
        with self.assertRaises(AttachmentValidationError):
            StockDocumentAttachmentService.add_attachment(
                document_id=self.doc.id,
                data=b"not a real file",
                content_type="",
            )
        mock_storage.put_object.assert_not_called()
