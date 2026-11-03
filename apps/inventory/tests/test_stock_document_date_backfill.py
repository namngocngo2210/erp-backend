"""Nắn ngày nghiệp vụ cho phiếu kho ghi lùi ngày trước khi có fix nối
`created_at` (ngày chứng từ) → `occurred_at` (ngày nghiệp vụ)."""
from __future__ import annotations

from datetime import datetime, UTC

import pytest
from django.db import connection

from apps.inventory.application.services.stock_document_date_backfill import (
    backfill_document_business_date,
)
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import (
    DocStatus,
    DocType,
    MovementType,
    StockDocument,
    StockMovement,
)

pytestmark = pytest.mark.django_db

# Ngày chứng từ người dùng nhập (hàng thật rời kho) và ngày duyệt phiếu.
DOC_DATE = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)
POSTED_AT = datetime(2026, 7, 6, 3, 0, tzinfo=UTC)


@pytest.fixture
def dest_warehouse(make_warehouse):
    return make_warehouse(name="Cửa hàng", code="KDB2")


def _legacy_transfer(warehouse, dest_warehouse, product, actor, qty=5):
    """Dựng lại đúng dữ liệu CŨ: phiếu ghi ngày 04/07, bút toán mang ngày duyệt
    06/07 (hành vi trước fix — service mới không tạo được trạng thái này nữa)."""
    doc = StockDocumentService.create_document(
        doc_type=DocType.TRANSFER,
        source_warehouse_id=warehouse.id,
        dest_warehouse_id=dest_warehouse.id,
        lines=[{"product_id": product.id, "quantity": qty}],
        user_id=actor.id,
        created_at=DOC_DATE,
    )
    StockDocumentService.submit_for_approval(document_id=doc.id, user_id=actor.id)
    StockDocumentService.approve_document(document_id=doc.id, user_id=actor.id)
    StockDocument.objects.filter(id=doc.id).update(occurred_at=None)
    StockMovement.objects.filter(reference_type="document", reference_id=doc.id).update(
        occurred_at=POSTED_AT, created_at=POSTED_AT
    )
    return doc


def _occurred(doc_id: int, movement_type: str):
    """Ngày nghiệp vụ của bút toán GỐC. Lọc `note=""` vì bút toán đảo lúc huỷ
    phiếu dùng CHUNG movement_type, chỉ khác note."""
    return list(
        StockMovement.objects.filter(
            reference_type="document",
            reference_id=doc_id,
            movement_type=movement_type,
            note="",
        ).values_list("occurred_at", flat=True)
    )


def test_backfill_moves_movements_to_document_date(
    warehouse, dest_warehouse, product, actor
):
    """Cả hai nhánh của phiếu chuyển (xuất kho nguồn + nhập kho đích) phải về
    ngày chứng từ, nếu không tab Xuất/Nhập hoàn của ngày đó vẫn trống."""
    doc = _legacy_transfer(warehouse, dest_warehouse, product, actor)

    result = backfill_document_business_date(connection)

    assert result["movements"] == 2
    assert _occurred(doc.id, MovementType.TRANSFER_OUT) == [DOC_DATE]
    assert _occurred(doc.id, MovementType.TRANSFER_IN) == [DOC_DATE]


def test_backfill_fills_document_occurred_at(warehouse, dest_warehouse, product, actor):
    """Phiếu chưa duyệt cũng phải có mốc nghiệp vụ, kẻo duyệt sau fix vẫn lệch."""
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
        created_at=DOC_DATE,
    )
    StockDocument.objects.filter(id=doc.id).update(occurred_at=None)

    result = backfill_document_business_date(connection)

    doc.refresh_from_db()
    assert result["documents"] == 1
    assert doc.occurred_at == DOC_DATE


def test_backfill_leaves_cancel_reversal_on_its_own_date(
    warehouse, dest_warehouse, product, actor
):
    """Huỷ phiếu là sự kiện của NGÀY HUỶ — kéo bút toán đảo về ngày chứng từ sẽ
    xoá một sự kiện có thật khỏi đúng ngày của nó (cùng luật với
    `occurred_at_backfill`)."""
    doc = _legacy_transfer(warehouse, dest_warehouse, product, actor)
    StockDocumentService.cancel_document(document_id=doc.id, user_id=actor.id)
    reversal_dates = list(
        StockMovement.objects.filter(
            reference_type="document", reference_id=doc.id
        )
        .exclude(note="")
        .values_list("occurred_at", flat=True)
    )

    backfill_document_business_date(connection)

    assert reversal_dates and all(d > DOC_DATE for d in reversal_dates)
    assert (
        list(
            StockMovement.objects.filter(reference_type="document", reference_id=doc.id)
            .exclude(note="")
            .values_list("occurred_at", flat=True)
        )
        == reversal_dates
    )


def test_backfill_skips_cancelled_documents(
    warehouse, dest_warehouse, product, actor
):
    """Phiếu đã huỷ: net đã về 0 ở mọi khung ngày — nắn nửa cặp bút toán chỉ làm
    phiếu 'sống lại' ở ngày chứng từ."""
    doc = _legacy_transfer(warehouse, dest_warehouse, product, actor)
    StockDocumentService.cancel_document(document_id=doc.id, user_id=actor.id)
    assert StockDocument.objects.get(id=doc.id).status == DocStatus.CANCELLED

    result = backfill_document_business_date(connection)

    assert result["movements"] == 0
    assert _occurred(doc.id, MovementType.TRANSFER_OUT) == [POSTED_AT]


def test_backfill_is_idempotent(warehouse, dest_warehouse, product, actor):
    """Chạy lại (migration replay / ngắt giữa chừng) không được nắn thêm lần nữa."""
    _legacy_transfer(warehouse, dest_warehouse, product, actor)

    backfill_document_business_date(connection)
    second = backfill_document_business_date(connection)

    assert second == {"documents": 0, "movements": 0}


def test_backfill_keeps_invariant_occurred_at_not_after_created_at(
    warehouse, dest_warehouse, product, actor
):
    """Phiếu ghi ngày TƯƠNG LAI so với lúc ghi sổ (dữ liệu cũ lọt qua vì trước
    fix không ai validate) không được nắn — phá bất biến là làm hỏng luôn cả
    partition pruning của `filter_occurred_between`."""
    doc = _legacy_transfer(warehouse, dest_warehouse, product, actor)
    StockDocument.objects.filter(id=doc.id).update(
        created_at=datetime(2026, 7, 9, 3, 0, tzinfo=UTC)
    )

    result = backfill_document_business_date(connection)

    assert result["movements"] == 0
    assert _occurred(doc.id, MovementType.TRANSFER_OUT) == [POSTED_AT]
