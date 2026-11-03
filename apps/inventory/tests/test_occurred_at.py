"""Cột ngày nghiệp vụ của bút toán kho — xem
docs/superpowers/specs/2026-07-22-scan-match-business-date-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC

import pytest
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.movement_window import (
    filter_occurred_between,
)
from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.application.services.transfer_document_history import (
    TransferDocumentHistoryService,
)
from apps.inventory.models import DocType, MovementType, StockMovement
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


# ---------------------------------------------------------------------------
# Ghi bút toán trực tiếp (gốc: test_movement_occurred_at.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_movement_defaults_occurred_at_to_write_time(warehouse, product):
    """Luồng thường không truyền gì → ngày nghiệp vụ xấp xỉ ngày ghi sổ."""
    before = timezone.now()
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=5,
        movement_type=MovementType.RECEIPT,
    )
    movement = StockMovement.objects.get(id=result.movement_id)
    assert movement.occurred_at is not None
    assert before <= movement.occurred_at <= timezone.now()
    # Bất biến toàn hệ thống.
    assert movement.occurred_at <= movement.created_at
    assert abs(movement.created_at - movement.occurred_at) < timedelta(seconds=5)


@pytest.mark.django_db
def test_movement_records_explicit_occurred_at(warehouse, product):
    """Ngày nghiệp vụ do caller chỉ định; ngày ghi sổ vẫn là bây giờ."""
    scanned_at = timezone.now() - timedelta(days=2)
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-1,
        movement_type=MovementType.SALE,
        occurred_at=scanned_at,
    )
    movement = StockMovement.objects.get(id=result.movement_id)
    assert movement.occurred_at == scanned_at
    assert movement.created_at - movement.occurred_at >= timedelta(days=2)


@pytest.mark.django_db
def test_movement_rejects_future_occurred_at(warehouse, product):
    """Bất biến occurred_at <= created_at: ngày tương lai là lỗi lập trình."""
    with pytest.raises(ValueError, match="tương lai"):
        StockCommandService.record_movement(
            warehouse_id=warehouse.id,
            product_id=product.id,
            qty_delta=-1,
            movement_type=MovementType.SALE,
            occurred_at=timezone.now() + timedelta(hours=1),
        )


# ---------------------------------------------------------------------------
# Mốc nghiệp vụ của phiếu kho: bút toán phải mang ngày HÀNG RỜI KHO, không phải
# ngày duyệt. (gốc: test_stock_document_occurred_at.py)
# ---------------------------------------------------------------------------


def _approve(doc_id: int, user_id: int):
    StockDocumentService.submit_for_approval(document_id=doc_id, user_id=user_id)
    return StockDocumentService.approve_document(document_id=doc_id, user_id=user_id)


def _issue_movement(doc_id: int) -> StockMovement:
    return StockMovement.objects.get(
        reference_type="document", reference_id=doc_id, movement_type=MovementType.ISSUE
    )


@pytest.mark.django_db
def test_issue_movement_uses_document_occurred_at(warehouse, product, actor):
    """Duyệt muộn 2 ngày không được đẩy sản lượng sang ngày duyệt."""
    scanned_at = timezone.now() - timedelta(days=2)
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
        occurred_at=scanned_at,
    )
    _approve(doc.id, actor.id)
    movement = _issue_movement(doc.id)
    assert movement.occurred_at == scanned_at
    assert movement.created_at > scanned_at


@pytest.mark.django_db
def test_manual_document_without_occurred_at_keeps_now(warehouse, product, actor):
    """Phiếu tay hiện có (occurred_at=None) giữ NGUYÊN hành vi cũ: bút toán mang lúc duyệt."""
    before = timezone.now()
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
    )
    assert doc.occurred_at is None
    _approve(doc.id, actor.id)
    assert _issue_movement(doc.id).occurred_at >= before


# ---------------------------------------------------------------------------
# NGÀY CHỨNG TỪ người dùng nhập trên form phiếu kho (`created_at`) chính là ngày
# nghiệp vụ. Không nối hai thứ này, phiếu ghi lùi ngày sẽ rơi vào ngày DUYỆT và
# biến mất khỏi khung lọc của trang Lịch sử nhập xuất (ca thật 2026-08-15: phiếu
# chuyển kho CK20260815-0001 ghi ngày 14/08 không hiện ở tab Xuất ngày 14/08).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_backdated_document_date_becomes_business_date(warehouse, product, actor):
    """Ngày chứng từ lùi 2 ngày → bút toán mang chính ngày đó, không phải ngày duyệt."""
    doc_date = timezone.now() - timedelta(days=2)
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
        created_at=doc_date,
    )
    assert doc.occurred_at == doc_date
    _approve(doc.id, actor.id)
    movement = _issue_movement(doc.id)
    assert movement.occurred_at == doc_date
    assert movement.created_at > doc_date


@pytest.mark.django_db
def test_explicit_occurred_at_wins_over_document_date(warehouse, product, actor):
    """Phiếu sinh tự động truyền thẳng mốc quét — ngày chứng từ không được đè lên."""
    scanned_at = timezone.now() - timedelta(days=3)
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
        created_at=timezone.now() - timedelta(days=1),
        occurred_at=scanned_at,
    )
    assert doc.occurred_at == scanned_at


@pytest.mark.django_db
def test_editing_draft_date_moves_business_date(warehouse, product, actor):
    """Sửa ngày chứng từ trên phiếu nháp phải kéo theo ngày nghiệp vụ."""
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
        created_at=timezone.now() - timedelta(days=1),
    )
    new_date = timezone.now() - timedelta(days=4)
    StockDocumentService.update_document(
        document_id=doc.id,
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        created_at=new_date,
    )
    doc.refresh_from_db()
    assert doc.occurred_at == new_date
    _approve(doc.id, actor.id)
    assert _issue_movement(doc.id).occurred_at == new_date


@pytest.mark.django_db
def test_backdated_transfer_shows_in_issue_history_on_document_date(
    warehouse, product, actor
):
    """Ca thật: phiếu CHUYỂN KHO ghi ngày hôm qua, duyệt hôm nay → tab Xuất của
    NGÀY CHỨNG TỪ phải thấy nó (trước fix: chỉ thấy ở ngày duyệt)."""
    dest = Warehouse.objects.create(name="Cửa hàng", code="KOC2")
    doc_date = timezone.now() - timedelta(days=1)
    business_date = timezone.localtime(doc_date).strftime("%Y-%m-%d")
    doc = StockDocumentService.create_document(
        doc_type=DocType.TRANSFER,
        source_warehouse_id=warehouse.id,
        dest_warehouse_id=dest.id,
        lines=[{"product_id": product.id, "quantity": 5}],
        user_id=actor.id,
        created_at=doc_date,
    )
    _approve(doc.id, actor.id)

    assert (
        TransferDocumentHistoryService.total_out(
            date_from=business_date, date_to=business_date
        )
        == 5
    )
    codes = [
        row["doc_code"]
        for row in TransferDocumentHistoryService.sku_documents_out(
            product_id=product.id, date_from=business_date, date_to=business_date
        )
    ]
    assert codes == [doc.code]


# ---------------------------------------------------------------------------
# Tab Xuất lọc theo NGÀY NGHIỆP VỤ của bút toán, không theo ngày ghi sổ.
# (gốc: test_dispatch_history_occurred_at.py)
# ---------------------------------------------------------------------------

OCCURRED_AT = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)  # 10:00 giờ VN 04/07
OCCURRED_DATE = "2026-07-04"


@pytest.fixture
def backdated_sale(warehouse, product):
    """Bút toán xuất: nghiệp vụ ngày 04/07, ghi sổ ngày 06/07."""
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-3,
        movement_type=MovementType.SALE,
        reference_type="order",
        reference_id=4242,
        occurred_at=OCCURRED_AT,
    )
    StockMovement.objects.filter(id=result.movement_id).update(
        created_at=datetime(2026, 7, 6, 3, 0, tzinfo=UTC)
    )
    return StockMovement.objects.get(id=result.movement_id)


@pytest.mark.django_db
def test_dispatch_history_finds_movement_on_business_date(backdated_sale):
    total = DispatchHistoryService.total_issued(
        date_from=OCCURRED_DATE, date_to=OCCURRED_DATE
    )
    assert total == 3


@pytest.mark.django_db
def test_dispatch_history_excludes_movement_on_write_date(backdated_sale):
    total = DispatchHistoryService.total_issued(
        date_from="2026-07-06", date_to="2026-07-06"
    )
    assert total == 0


@pytest.mark.django_db
def test_dispatch_history_last_at_is_business_date(backdated_sale):
    groups = list(
        DispatchHistoryService.order_groups(
            date_from=OCCURRED_DATE, date_to=OCCURRED_DATE
        )
    )
    assert len(groups) == 1
    assert groups[0]["last_at"] == OCCURRED_AT


# 18:00 UTC 04/07 = 01:00 giờ VN 05/07 — cách xa nửa đêm UTC lẫn nửa đêm VN đủ để
# phân biệt "quy đổi theo UTC" với "quy đổi theo Asia/Bangkok" (khác OCCURRED_AT ở
# trên chỉ cách nửa đêm giờ VN, không phân biệt được 2 cách quy đổi).
LATE_NIGHT_OCCURRED_AT = datetime(2026, 7, 4, 18, 0, tzinfo=UTC)
LATE_NIGHT_DATE = "2026-07-05"
UTC_INSTANT_DATE = "2026-07-04"


@pytest.fixture
def late_night_sale(warehouse, product):
    """Bút toán xuất: nghiệp vụ 18:00 UTC 04/07, tức 01:00 giờ VN 05/07."""
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-2,
        movement_type=MovementType.SALE,
        reference_type="order",
        reference_id=4243,
        occurred_at=LATE_NIGHT_OCCURRED_AT,
    )
    return StockMovement.objects.get(id=result.movement_id)


@pytest.mark.django_db
def test_dispatch_history_uses_bangkok_date_not_utc_date(late_night_sale):
    """18:00 UTC 04/07 là 01:00 giờ VN 05/07 → phải lên báo cáo ngày 05/07, KHÔNG
    phải 04/07 (chốt việc quy đổi ngày dùng giờ Bangkok, không phải UTC)."""
    total_local_date = DispatchHistoryService.total_issued(
        date_from=LATE_NIGHT_DATE, date_to=LATE_NIGHT_DATE
    )
    assert total_local_date == 2

    total_utc_instant_date = DispatchHistoryService.total_issued(
        date_from=UTC_INSTANT_DATE, date_to=UTC_INSTANT_DATE
    )
    assert total_utc_instant_date == 0


@pytest.mark.django_db
def test_filter_occurred_between_accepts_unpadded_date_string(backdated_sale):
    """"2026-7-4" (không zero-pad) phải được chấp nhận y hệt "2026-07-04" — đây
    chính là dạng `_valid_date` (view, dùng `strptime("%Y-%m-%d")`) cho qua, nên
    `_as_date` không được chặt hơn `_valid_date` kẻo biến request hợp lệ thành
    lỗi 500."""
    padded = filter_occurred_between(
        StockMovement.objects.all(), date_from="2026-07-04", date_to="2026-07-04"
    )
    unpadded = filter_occurred_between(
        StockMovement.objects.all(), date_from="2026-7-4", date_to="2026-7-4"
    )
    assert list(unpadded.values_list("id", flat=True)) == list(
        padded.values_list("id", flat=True)
    )
    assert unpadded.count() == 1


@pytest.mark.django_db
def test_filter_occurred_between_treats_aware_datetime_as_bangkok_day(
    late_night_sale,
):
    """Truyền thẳng `datetime` UTC 18:00 04/07 (= 01:00 giờ VN 05/07) làm
    `date_from`/`date_to` phải được hiểu là ngày Bangkok 05/07, KHÔNG phải ngày
    UTC 04/07 — `datetime` là subclass của `date` nên nhánh `isinstance(value,
    date)` phải KHÔNG được bắt nhầm nó trước khi quy đổi timezone."""
    result = filter_occurred_between(
        StockMovement.objects.all(),
        date_from=LATE_NIGHT_OCCURRED_AT,
        date_to=LATE_NIGHT_OCCURRED_AT,
    )
    assert result.count() == 1
    assert result.first().id == late_night_sale.id


class OccurredAtDbDefaultTests(TransactionTestCase):
    """DEFAULT ở TẦNG DB của `stock_movements.occurred_at`.

    Cửa sổ deploy không nguyên tử: code CŨ (worker/beat chưa kịp recreate) vẫn INSERT
    mà không nêu tên cột `occurred_at`. Nếu cột chỉ có `default=timezone.now` ở tầng
    Python thì những INSERT đó hoặc nổ NOT NULL, hoặc để lại NULL rồi bị migration
    dập cho một mốc thời gian LỚN HƠN `created_at` — vỡ bất biến
    `occurred_at <= created_at` và biến mất khỏi mọi khung ngày của
    `movement_window.filter_occurred_between` mà không hề báo lỗi.

    Các test dưới đây mô phỏng đúng đường ghi đó bằng raw SQL (giống các test
    partition trong test_db_schema.py), nên chúng canh chính lớp bug này.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="W-DEF", name="Kho Default")
        self.product = Product.objects.create(name="SP Default", sku="DEF-1")

    def _raw_insert_without_occurred_at(self) -> int:
        """INSERT y như code CŨ: không hề nhắc tới cột occurred_at."""
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO stock_movements "
                "(warehouse_id, product_id, qty_delta, movement_type, state, "
                " balance_after, note, created_at) "
                "VALUES (%s, %s, 1, 'receipt', 'sellable', 1, '', now()) "
                "RETURNING id;",
                [self.warehouse.id, self.product.id],
            )
            return cur.fetchone()[0]

    def test_raw_insert_omitting_occurred_at_succeeds_and_keeps_invariant(self):
        movement_id = self._raw_insert_without_occurred_at()

        with connection.cursor() as cur:
            cur.execute(
                "SELECT occurred_at IS NOT NULL, occurred_at <= created_at "
                "FROM stock_movements WHERE id = %s;",
                [movement_id],
            )
            row = cur.fetchone()
        assert row is not None, "dòng vừa INSERT phải tồn tại"
        not_null, within_invariant = row
        assert not_null, "DEFAULT now() ở tầng DB phải lấp cột, không được để NULL"
        assert within_invariant, "vỡ bất biến occurred_at <= created_at"

    def test_column_has_database_level_default_now(self):
        """Chốt chặn chống hồi quy: AlterField của Django DROP DEFAULT giữa chừng."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'stock_movements' AND column_name = 'occurred_at';"
            )
            is_nullable, column_default = cur.fetchone()
        assert is_nullable == "NO"
        assert column_default == "now()"

    def test_no_movement_violates_invariant_after_write_paths(self):
        # Đường ghi 1: ORM, không truyền ngày nghiệp vụ.
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=5,
            movement_type=MovementType.RECEIPT,
        )
        # Đường ghi 2: ORM, ngày nghiệp vụ lùi về quá khứ (mã quét về muộn).
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=-1,
            movement_type=MovementType.SALE,
            occurred_at=timezone.now() - timedelta(days=3),
        )
        # Đường ghi 3: raw SQL bỏ trống cột (code cũ trong cửa sổ deploy).
        self._raw_insert_without_occurred_at()

        with connection.cursor() as cur:
            cur.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM stock_movements "
                "WHERE occurred_at > created_at);"
            )
            assert cur.fetchone()[0], "có bút toán vi phạm occurred_at <= created_at"
