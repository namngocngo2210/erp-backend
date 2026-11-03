"""Ràng buộc DB của hoàn trả (gộp từ test_return_note_shipping_code_unique +
test_return_type_check_constraint, tái cấu trúc 2026-08-05).

Invariant mã vận đơn: 1 mã ↔ 1 lần hoàn chưa huỷ (chống cộng tồn 2 lần).

Ràng buộc DB nằm ở ReturnEntry (`returnentry_shipcode_draft_uniq`); phiếu ngày
(ReturnNote) không giữ mã vận đơn nữa.

Check constraint loại hoàn — nay nằm ở DÒNG (ReturnNoteLine.return_type).

Phiếu ngày (ReturnNote) không còn cột return_type để kế thừa, nên loại hoàn là
bắt buộc và phải thuộc tập choices; rỗng bị DB từ chối.
"""
from __future__ import annotations

from datetime import date
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.models import (
    ReturnEntry,
    ReturnNote,
    ReturnNoteLine,
    ReturnStatus,
    ReturnType,
)
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


def _make_wh_and_product():
    wh = Warehouse.objects.create(name="WH T1", code="WHT1")
    p = Product.objects.create(name="SP T1", sku="SKU-T1")
    return wh, p


def _make_note(wh, code="PTH-T1"):
    return ReturnNote.objects.create(
        code=code, warehouse=wh, doc_date=date(2026, 7, 1)
    )


class ShippingCodeUniqueConstraintTests(TestCase):
    def test_duplicate_draft_unmatched_shipping_code_rejected_at_db(self):
        wh, _p = _make_wh_and_product()
        note = _make_note(wh)
        ReturnEntry.objects.create(
            return_note=note, shipping_code="VN123", status=ReturnStatus.DRAFT
        )
        with self.assertRaises(IntegrityError):
            ReturnEntry.objects.create(
                return_note=note, shipping_code="VN123", status=ReturnStatus.DRAFT
            )

    def test_posted_or_matched_duplicates_still_allowed(self):
        wh, _p = _make_wh_and_product()
        note = _make_note(wh)
        ReturnEntry.objects.create(
            return_note=note, shipping_code="VN456", status=ReturnStatus.POSTED
        )
        # cùng mã nhưng nhóm cũ đã POSTED → cho phép draft mới ở tầng DB
        # (chặn nghiệp vụ nằm ở pre-check của create_entry).
        ReturnEntry.objects.create(
            return_note=note, shipping_code="VN456", status=ReturnStatus.DRAFT
        )
        self.assertEqual(ReturnEntry.objects.filter(shipping_code="VN456").count(), 2)

    def test_create_entry_service_rejects_duplicate_code(self):
        wh, p = _make_wh_and_product()
        lines = [{"product_id": p.id, "quantity": 1, "return_type": "delivery_returned"}]
        ReturnEntryService.create_entry(
            warehouse_id=wh.id, lines=lines, shipping_code="VN789"
        )
        with self.assertRaises(ValueError):
            ReturnEntryService.create_entry(
                warehouse_id=wh.id, lines=lines, shipping_code="VN789"
            )

    def test_empty_draft_holding_the_code_is_recycled(self):
        """Nháp RỖNG không chặn được lần quét thật: nó không có bút toán nào và không còn
        cửa nào ghi sổ → tự huỷ, nhường mã (audit 2026-07-14 vòng 3)."""
        wh, p = _make_wh_and_product()
        stale = ReturnEntryService.create_entry(
            warehouse_id=wh.id, lines=[], shipping_code="VN790"
        )

        entry = ReturnEntryService.create_entry(
            warehouse_id=wh.id,
            lines=[{"product_id": p.id, "quantity": 1, "return_type": "delivery_returned"}],
            shipping_code="VN790",
        )

        stale.refresh_from_db()
        self.assertEqual(stale.status, ReturnStatus.CANCELLED)
        self.assertEqual(entry.lines.count(), 1)


class ServiceIntegrityErrorMappingTests(TestCase):
    """Cô lập nhánh map IntegrityError trong create_entry.

    Test tuần tự ở trên không chạm tới nhánh này vì pre-check đã raise trước —
    nhánh chỉ kích hoạt khi 2 request đồng thời cùng vượt pre-check (race thật).
    Mock ``ReturnEntry.objects.create`` raise IntegrityError tổng hợp để mô phỏng.
    """

    def test_shipcode_constraint_violation_maps_to_value_error(self):
        wh, _p = _make_wh_and_product()
        exc = IntegrityError(
            'duplicate key value violates unique constraint '
            '"returnentry_shipcode_draft_uniq"\n'
            "DETAIL:  Key (shipping_code)=(VN999) already exists."
        )
        with mock.patch.object(ReturnEntry.objects, "create", side_effect=exc):
            with self.assertRaises(ValueError) as ctx:
                ReturnEntryService.create_entry(
                    warehouse_id=wh.id, lines=[], shipping_code="VN999"
                )
        # Phải là message thân thiện về mã vận đơn.
        self.assertIn("VN999", str(ctx.exception))
        self.assertIn("đã được ghi nhận", str(ctx.exception))

    def test_other_integrity_error_is_not_swallowed(self):
        """IntegrityError khác (không phải constraint mã vận đơn) phải nổi lên
        nguyên trạng, không bị map nhầm thành ValueError 'mã đã được ghi nhận'."""
        wh, _p = _make_wh_and_product()
        exc = IntegrityError(
            'duplicate key value violates unique constraint '
            '"stock_return_entries_pkey"'
        )
        with mock.patch.object(ReturnEntry.objects, "create", side_effect=exc):
            with self.assertRaises(IntegrityError):
                ReturnEntryService.create_entry(
                    warehouse_id=wh.id, lines=[], shipping_code="VN888"
                )


def _make_entry():
    wh = Warehouse.objects.create(name="WH RT", code="WHRT")
    note = ReturnNote.objects.create(
        code="PTH-RT", warehouse=wh, doc_date=date(2026, 7, 1)
    )
    return ReturnEntry.objects.create(return_note=note)


class ReturnTypeCheckConstraintTests(TestCase):
    def test_invalid_return_type_rejected_at_db(self):
        entry = _make_entry()
        product = Product.objects.create(name="SP RT", sku="RT-1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReturnNoteLine.objects.create(
                    entry=entry, product=product, quantity=1, return_type="bogus"
                )

    def test_blank_return_type_rejected_at_db(self):
        """Trước đây rỗng được phép (kế thừa loại của phiếu); phiếu ngày không có
        loại nữa → rỗng là dữ liệu sai bucket tồn kho, chặn ở DB."""
        entry = _make_entry()
        product = Product.objects.create(name="SP RT", sku="RT-2")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReturnNoteLine.objects.create(
                    entry=entry, product=product, quantity=1, return_type=""
                )

    def test_all_choice_values_allowed(self):
        entry = _make_entry()
        for i, value in enumerate(ReturnType.values):
            product = Product.objects.create(name=f"SP RT {i}", sku=f"RT-OK-{i}")
            line = ReturnNoteLine.objects.create(
                entry=entry, product=product, quantity=1, return_type=value
            )
            self.assertEqual(line.return_type, value)
