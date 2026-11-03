"""Test 3 `ExportDefinition` của app inventory: `stock`, `dispatch_history`,
`return_history` — Task 15 của dự án "xuất file chạy nền".

Mỗi definition CHUYỂN NGUYÊN logic từ 1 view đồng bộ cũ (cả 3 view + test tham
chiếu bên dưới đã xoá ở Task 18, xem
docs/.superpowers/sdd/2026-08-06-async-export-jobs/task-18-report.md):
- `stock`            ← `stock_views.export_stock` (test tham chiếu gốc: `test_stock_query.py::StockExportTests`)
- `dispatch_history` ← `flow_history_views.issue_export` (test tham chiếu gốc: `test_flow_history_views.py`)
- `return_history`   ← `flow_history_views.return_export` (test tham chiếu gốc: `test_flow_history_views.py`)

Test ở đây gọi thẳng `definition.write(params, writer, progress)` với một writer
giả gom giá trị thô (không qua openpyxl) — nhanh hơn round-trip .xlsx thật và
kiểm được đúng GIÁ TRỊ PYTHON trước khi bị `clean_excel_cell` xử lý, đúng lớp
cần test ở tầng definition. Lớp writer thật (`StreamingXlsxWriter`) đã có test
riêng ở `apps/exports/tests/test_writers.py`.
"""
from __future__ import annotations

import pytest
from rest_framework.exceptions import ValidationError

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.exports.application.registry import get_definition
from apps.inventory.application.services.flow_note_service import FlowNoteService
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    COMBO_BACKFILL_ISSUE_NOTE,
    COMBO_BACKFILL_RELEASE_NOTE,
)
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import (
    DocType,
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnType,
    StockState,
)
from apps.orders.models import Order
from apps.catalog.models import Product, ProductComponent
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


class _RecordingWriter:
    """Writer giả — gom `write_header`/`write_row` thành list thô để assert."""

    def __init__(self):
        self.headers: list = []
        self.rows: list[list] = []

    def write_header(self, headers):
        self.headers = list(headers)

    def write_row(self, values):
        self.rows.append(list(values))


def _write(definition, params) -> _RecordingWriter:
    writer = _RecordingWriter()
    progressed: list[int] = []
    definition.write(params, writer, progressed.append)
    # progress(n) phải được gọi ít nhất 1 lần và đơn điệu tăng — hợp đồng writer
    # (progress vừa cập nhật tiến độ vừa là điểm kiểm huỷ).
    assert progressed == sorted(progressed)
    if writer.rows:
        assert progressed[-1] == len(writer.rows)
    return writer


# ─────────────────────────── Đăng ký + validate chung ───────────────────────────


def test_stock_requires_warehouse_id():
    definition = get_definition("stock")
    with pytest.raises(ValidationError):
        definition.validate(None, {})


def test_stock_validate_rejects_unknown_warehouse():
    definition = get_definition("stock")
    with pytest.raises(ValidationError):
        definition.validate(None, {"warehouse_id": 999999})


def test_stock_rejects_over_cap(monkeypatch):
    """Bám brief Step 1, với 2 khác biệt bắt buộc so với snippet gốc:

    1. `warehouse_id=1` cứng đổi thành id thật vừa tạo — brief không seed sẵn
       kho id=1 trong migration nên `validate` (đúng theo Step 3: "kiểm tra kho
       tồn tại") sẽ raise nếu dùng id cứng trên DB test rỗng.
    2. Kho PHẢI có ít nhất 1 dòng tồn thật. Bản đầu tiên của test này tạo kho
       RỖNG rồi hạ `_STOCK_MAX_ROWS` về 0 và kỳ vọng raise — nhưng
       `estimate()` so `count > _STOCK_MAX_ROWS`; kho rỗng thì `0 > 0` là
       False nên KHÔNG raise (đúng ngữ nghĩa gốc `len(items) > cap`, kho rỗng
       không có gì để cắt bớt) → test đỏ vì THIẾU DỮ LIỆU, không phải vì code
       sai (xác nhận qua controller review). Phải có ≥1 dòng tồn để
       `count=1 > cap=0` thật sự đúng nghĩa vượt trần.
    """
    from apps.inventory.models import StockItem

    warehouse = Warehouse.objects.create(code="WH-CAP", name="Kho Cap")
    product = Product.objects.create(name="SP Cap", sku="SKU-CAP")
    StockItem.objects.create(
        warehouse=warehouse, product=product, state=StockState.SELLABLE, quantity=1
    )

    from apps.inventory import exports_definitions

    definition = get_definition("stock")
    params = definition.validate(None, {"warehouse_id": warehouse.id})
    monkeypatch.setattr(exports_definitions, "_STOCK_MAX_ROWS", 0)
    with pytest.raises(ValidationError):
        definition.estimate(params)


# ─────────────────────────── stock: write() ───────────────────────────


def _stock(warehouse, product, state, quantity):
    from apps.inventory.models import StockItem

    return StockItem.objects.create(
        warehouse=warehouse, product=product, state=state, quantity=quantity
    )


class TestStockExportWrite:
    def setup_method(self):
        self.warehouse = Warehouse.objects.create(code="WH-EX", name="Kho Xuất")
        self.other_warehouse = Warehouse.objects.create(code="WH-OT", name="Kho Khác")
        self.product = Product.objects.create(name="Áo thun", sku="SKU-A")
        self.other_product = Product.objects.create(name="Quần jean", sku="SKU-B")
        self.definition = get_definition("stock")

    def _params(self, **raw):
        return self.definition.validate(None, {"warehouse_id": self.warehouse.id, **raw})

    def test_header_matches_screen_column_order(self):
        _stock(self.warehouse, self.product, StockState.SELLABLE, 1)
        writer = _write(self.definition, self._params())
        assert writer.headers == [
            "Kho", "SKU", "Tên sản phẩm", "Bán được", "Bảo hành", "Lỗi", "Tổng", "RR3", "RR7",
        ]

    def test_pivots_three_states_into_columns_with_total(self):
        _stock(self.warehouse, self.product, StockState.SELLABLE, 10)
        _stock(self.warehouse, self.product, StockState.WARRANTY, 3)
        _stock(self.warehouse, self.product, StockState.DEFECT, 2)

        writer = _write(self.definition, self._params())

        assert len(writer.rows) == 1
        assert writer.rows[0][:7] == ["Kho Xuất", "SKU-A", "Áo thun", 10, 3, 2, 15]

    def test_only_selected_warehouse_is_exported(self):
        _stock(self.warehouse, self.product, StockState.SELLABLE, 5)
        _stock(self.other_warehouse, self.other_product, StockState.SELLABLE, 7)

        writer = _write(self.definition, self._params())

        assert [r[1] for r in writer.rows] == ["SKU-A"]

    def test_search_filter_is_applied(self):
        _stock(self.warehouse, self.product, StockState.SELLABLE, 5)
        _stock(self.warehouse, self.other_product, StockState.SELLABLE, 7)

        writer = _write(self.definition, self._params(search="SKU-B"))

        assert [r[1] for r in writer.rows] == ["SKU-B"]

    def test_file_name_carries_warehouse_code(self):
        params = self._params()
        assert self.definition.file_name(params).startswith("ton-kho-WH-EX-")
        assert self.definition.file_name(params).endswith(".xlsx")

    def test_write_truncates_and_warns_when_cap_exceeded_after_job_created(self, monkeypatch):
        """TOCTOU do kiến trúc async sinh ra (fix round 1, reviewer): `estimate()`
        đếm 1 LẦN, đồng bộ, lúc tạo job; `write()` chạy sau đó ở worker, có thể
        trễ vài phút — kho có thể phình vượt trần GIỮA hai mốc đó, và job đã tạo
        rồi thì không huỷ được nữa. Test này KHÔNG gọi `estimate()` — mô phỏng
        đúng ca "trần đã đổi sau khi job được tạo": `write()` phải tự áp lại
        LIMIT (không kéo cả resultset vào RAM) và cảnh báo thay vì âm thầm cắt.
        """
        from apps.inventory import exports_definitions

        monkeypatch.setattr(exports_definitions, "_STOCK_MAX_ROWS", 2)
        for i in range(3):
            product = Product.objects.create(name=f"SP Cap {i}", sku=f"SKU-CAP-{i}")
            _stock(self.warehouse, product, StockState.SELLABLE, 1)

        writer = _write(self.definition, self._params())

        data_rows = [r for r in writer.rows if str(r[1]).startswith("SKU-CAP-")]
        assert len(data_rows) == 2, "write() phải tự cắt về đúng trần, không kéo cả 3 dòng"
        warning_rows = [r for r in writer.rows if str(r[0]).startswith("⚠️")]
        assert len(warning_rows) == 1, "phải có đúng 1 dòng cảnh báo, không raise lỗi"
        assert "3 dòng tồn" in warning_rows[0][0]
        assert "2 dòng đầu" in warning_rows[0][0]


# ─────────────────────────── dispatch/return history: fixtures dùng chung ──────


def _seed_flow():
    """Bản sao NGUYÊN VẸN của `test_flow_history_views.py::_seed` — cùng kịch bản
    dữ liệu để so hàng ngang được với các test view đồng bộ đã có."""
    channel = Channel.objects.create(code="lp-fh-def", name="FH-DEF")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000011", phone_normalized="0900000011"
    )
    wh = Warehouse.objects.create(code="WH-FH-DEF", name="Kho A Def")
    p1 = Product.objects.create(sku="SKU1DEF", name="Áo")
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name="K",
        phone="0900000011",
        order_code="DH001DEF",
        invoice_code="HD001DEF",
        shipping_code="VD001DEF",
        total_amount="1000",
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=order.id,
        lines=[
            {"product_id": p1.id, "quantity": 2, "return_type": ReturnType.DELIVERY_RETURNED}
        ],
    )
    PendingShipmentScan.objects.create(
        code="VDUNMATCHEDDEF", kind=PendingScanKind.DISPATCH, warehouse=wh,
        status=PendingScanStatus.PENDING,
    )
    PendingShipmentScan.objects.create(
        code="VDRETDEF", kind=PendingScanKind.RETURN, warehouse=wh,
        status=PendingScanStatus.FAILED, error="lỗi",
    )
    return wh, order, p1, entry


def _raw_filters(**overrides) -> dict:
    base = {"date_from": "", "date_to": "", "warehouse_id": None, "product": "",
            "product_ids": "", "order": "", "carrier": ""}
    base.update(overrides)
    return base


def _seed_two_skus():
    """Một đơn đã xuất kho gồm SP-A ×2 và SP-B ×1."""
    channel = Channel.objects.create(code="lp-split", name="SPLIT")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000022", phone_normalized="0900000022"
    )
    wh = Warehouse.objects.create(code="WH-SPLIT", name="Kho Split")
    a = Product.objects.create(sku="SP-A", name="Sản phẩm A")
    b = Product.objects.create(sku="SP-B", name="Sản phẩm B")
    order = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000022",
        order_code="DH-SPLIT", invoice_code="HD-SPLIT", shipping_code="VD-SPLIT",
        total_amount="1000",
    )
    for product, qty in ((a, 2), (b, 1)):
        StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=product.id, qty_delta=-qty,
            movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
        )
    return wh, order, a, b


def _seed_combo(sku_suffix: str):
    """COMBO-X = SP-A ×1 + SP-B ×2; trả (kho, đơn, combo) — CHƯA ghi movement nào."""
    channel = Channel.objects.create(code=f"lp-combo-{sku_suffix}", name="COMBO")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000033", phone_normalized="0900000033"
    )
    wh = Warehouse.objects.create(code=f"WH-CB-{sku_suffix}", name="Kho Combo")
    combo = Product.objects.create(sku="COMBO-X", name="Combo X")
    a = Product.objects.create(sku="SP-A", name="Sản phẩm A")
    b = Product.objects.create(sku="SP-B", name="Sản phẩm B")
    ProductComponent.objects.create(parent=combo, component=a, quantity=1)
    ProductComponent.objects.create(parent=combo, component=b, quantity=2)
    order = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000033",
        order_code=f"DH-CB-{sku_suffix}", invoice_code=f"HD-CB-{sku_suffix}",
        shipping_code=f"VD-CB-{sku_suffix}", total_amount="1000",
    )
    return wh, order, combo


def _seed_combo_dispatch():
    """Đơn xuất kho 2 combo."""
    wh, order, combo = _seed_combo("D")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    return wh, order, combo


def _seed_retail_and_combo_dispatch():
    """SP-A vừa bán lẻ (đơn X ×3) vừa nằm trong COMBO-X ×1 (đơn Y mua 2 combo).

    Dùng để khoá `merge_sku_rows` gộp đúng 1 dòng SP-A: SL xuất = 3 (lẻ) + 2×1
    (combo) = 5, còn "Trong đó từ combo" chỉ tính phần combo = 2, không tính
    phần bán lẻ."""
    channel = Channel.objects.create(code="lp-retail-combo", name="RETAIL-COMBO")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000044", phone_normalized="0900000044"
    )
    wh = Warehouse.objects.create(code="WH-RC", name="Kho Retail Combo")
    combo = Product.objects.create(sku="COMBO-X", name="Combo X")
    a = Product.objects.create(sku="SP-A", name="Sản phẩm A")
    b = Product.objects.create(sku="SP-B", name="Sản phẩm B")
    ProductComponent.objects.create(parent=combo, component=a, quantity=1)
    ProductComponent.objects.create(parent=combo, component=b, quantity=2)
    order_x = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000044",
        order_code="DH-RC-X", invoice_code="HD-RC-X", shipping_code="VD-RC-X",
        total_amount="1000",
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=a.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order_x.id,
    )
    order_y = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000044",
        order_code="DH-RC-Y", invoice_code="HD-RC-Y", shipping_code="VD-RC-Y",
        total_amount="1000",
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order_y.id,
    )
    return wh, order_x, order_y, combo, a, b


def _seed_two_combos_sharing_component_dispatch():
    """COMBO-X = SP-A ×1, COMBO-Y = SP-A ×3; khách mua 2 combo mỗi loại trong
    cùng một đơn → SP-A phải cộng dồn từ CẢ HAI combo vào cùng 1 dòng:
    2×1 + 2×3 = 8."""
    channel = Channel.objects.create(code="lp-two-combo", name="TWO-COMBO")
    customer = Customer.objects.create(
        full_name="K", phone_raw="0900000055", phone_normalized="0900000055"
    )
    wh = Warehouse.objects.create(code="WH-TC", name="Kho Two Combo")
    a = Product.objects.create(sku="SP-A", name="Sản phẩm A")
    combo_x = Product.objects.create(sku="COMBO-X", name="Combo X")
    combo_y = Product.objects.create(sku="COMBO-Y", name="Combo Y")
    ProductComponent.objects.create(parent=combo_x, component=a, quantity=1)
    ProductComponent.objects.create(parent=combo_y, component=a, quantity=3)
    order = Order.objects.create(
        channel=channel, customer=customer, name="K", phone="0900000055",
        order_code="DH-TC", invoice_code="HD-TC", shipping_code="VD-TC",
        total_amount="1000",
    )
    for combo in (combo_x, combo_y):
        StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=combo.id, qty_delta=-2,
            movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
        )
    return wh, order, a, combo_x, combo_y


def _seed_return_two_skus():
    """Phiếu hoàn gồm SP-A ×2 và SP-B ×1 (đã xuất trước đó rồi mới hoàn)."""
    wh, order, a, b = _seed_two_skus()
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=order.id,
        lines=[
            {"product_id": a.id, "quantity": 2, "return_type": ReturnType.DELIVERY_RETURNED},
            {"product_id": b.id, "quantity": 1, "return_type": ReturnType.DELIVERY_RETURNED},
        ],
    )
    return wh, order


def _seed_return_combo():
    """Phiếu hoàn 2 combo; COMBO-X = SP-A ×1 + SP-B ×2."""
    wh, order, combo = _seed_combo("R")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id,
        source_order_id=order.id,
        lines=[{"product_id": combo.id, "quantity": 2, "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    return wh, order, combo


# ─────────────────────────── dispatch_history: validate/estimate/file_name ─────


class TestDispatchHistoryExportContract:
    def setup_method(self):
        self.definition = get_definition("dispatch_history")

    def test_validate_rejects_bad_date(self):
        with pytest.raises(ValidationError):
            self.definition.validate(None, _raw_filters(date_from="31-12-2026"))

    def test_validate_parses_product_ids_csv(self):
        params = self.definition.validate(
            None, _raw_filters(product_ids="1,5,x,9")
        )
        assert params["product_ids"] == [1, 5, 9]

    def test_validate_group_sku_flag(self):
        assert self.definition.validate(None, _raw_filters())["group"] == ""
        assert self.definition.validate(None, _raw_filters(group="sku"))["group"] == "sku"

# ─────────────────────────── dispatch_history: write() nhánh theo đơn ──────────


class TestDispatchHistoryExportWriteOrders:
    def setup_method(self):
        self.definition = get_definition("dispatch_history")

    def _params(self, warehouse_id=None, **overrides):
        return self.definition.validate(None, _raw_filters(warehouse_id=warehouse_id, **overrides))

    def test_header_matches_view(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        assert writer.headers == [
            "Thời gian", "Mã hóa đơn", "Mã vận đơn", "Kênh KiotViet", "Kho",
            "Người thực hiện", "SKU", "Tên sản phẩm", "Thuộc combo", "Ghi chú",
            "Số SKU", "SL xuất",
        ]

    def test_merges_matched_scan_note_into_order_row(self):
        """Mirror `test_issue_export_merges_matched_scan_note_into_order_row`."""
        wh, order, _p1, _entry = _seed_flow()
        PendingShipmentScan.objects.create(
            code=order.shipping_code, kind=PendingScanKind.DISPATCH, warehouse=wh,
            status=PendingScanStatus.MATCHED, matched_order=order, note="giao lại lần 2",
        )
        writer = _write(self.definition, self._params())
        vd_rows = [row for row in writer.rows if row[2] == order.shipping_code]
        assert len(vd_rows) == 1
        assert vd_rows[0][1] == order.invoice_code
        assert vd_rows[0][9] == "giao lại lần 2"

    def test_no_duplicate_when_failed_scan_shares_dispatched_code(self):
        """Mirror `test_issue_export_no_duplicate_when_failed_scan_shares_dispatched_code`."""
        wh, order, _p1, _entry = _seed_flow()
        PendingShipmentScan.objects.create(
            code=order.shipping_code, kind=PendingScanKind.DISPATCH, warehouse=wh,
            status=PendingScanStatus.FAILED, error="đã xuất kho trước đó",
            note="kiểm tra lại giúp",
        )
        writer = _write(self.definition, self._params())
        vd_rows = [row for row in writer.rows if row[2] == order.shipping_code]
        assert len(vd_rows) == 1
        assert vd_rows[0][1] == order.invoice_code
        assert vd_rows[0][9] == "kiểm tra lại giúp"

    def test_joins_notes_of_repeated_scans_same_code(self):
        """Mirror `test_issue_export_joins_notes_of_repeated_scans_same_code`."""
        wh, order, _p1, _entry = _seed_flow()
        PendingShipmentScan.objects.create(
            code=order.shipping_code, kind=PendingScanKind.DISPATCH, warehouse=wh,
            status=PendingScanStatus.FAILED, note="kiểm tra lại",
        )
        PendingShipmentScan.objects.create(
            code=order.shipping_code, kind=PendingScanKind.DISPATCH, warehouse=wh,
            status=PendingScanStatus.MATCHED, matched_order=order, note="giao lại lần 3",
        )
        writer = _write(self.definition, self._params())
        vd_rows = [row for row in writer.rows if row[2] == order.shipping_code]
        assert len(vd_rows) == 1
        note_cell = vd_rows[0][9] or ""
        assert "kiểm tra lại" in note_cell
        assert "giao lại lần 3" in note_cell

    def test_row_note_merges_with_scan_note(self):
        """Ghi chú của DÒNG (bảng `stock_flow_notes`) + ghi chú mã quét cùng một ô."""
        wh, order, _p1, _entry = _seed_flow()
        FlowNoteService.update(
            target_type="order_dispatch", target_id=order.id,
            note="đóng thiếu 1", user_id=None,
        )
        PendingShipmentScan.objects.create(
            code=order.shipping_code, kind=PendingScanKind.DISPATCH, warehouse=wh,
            status=PendingScanStatus.MATCHED, matched_order=order, note="giao lại lần 2",
        )
        writer = _write(self.definition, self._params())
        vd_rows = [row for row in writer.rows if row[2] == order.shipping_code]
        assert vd_rows[0][9] == "đóng thiếu 1; giao lại lần 2"

    def test_row_note_alone_reaches_file(self):
        _wh, order, _p1, _entry = _seed_flow()
        FlowNoteService.update(
            target_type="order_dispatch", target_id=order.id,
            note="khách đổi size", user_id=None,
        )
        writer = _write(self.definition, self._params())
        vd_rows = [row for row in writer.rows if row[2] == order.shipping_code]
        assert vd_rows[0][9] == "khách đổi size"

    def test_pending_unmatched_code_appears(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        pending_rows = [row for row in writer.rows if row[2] == "VDUNMATCHEDDEF"]
        assert len(pending_rows) == 1
        assert pending_rows[0][1] == "Chờ khớp"

    def test_footer_totals(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        # 5 dòng Tổng: 4 dòng ghi sổ cũ (đổi nhãn) + 1 dòng "Tổng SL các dòng
        # trong file" (cộng đúng các dòng thực có trong file).
        footer_labels = [row[0] for row in writer.rows[-5:]]
        assert footer_labels == [
            "Tổng theo đơn (SKU ghi sổ)", "Tổng phiếu xuất kho (SKU ghi sổ)",
            "Tổng phiếu chuyển kho (SKU ghi sổ)", "Tổng cộng (SKU ghi sổ)",
            "Tổng SL các dòng trong file",
        ]
        # SL xuất theo đơn (cột cuối) = 5 (mirror seed: qty_delta=-5).
        assert writer.rows[-5][-1] == 5
        assert writer.rows[-2][-1] == 5  # Tổng cộng (SKU ghi sổ) = sale(5) + doc(0) + transfer(0)
        assert writer.rows[-1][-1] == 5  # Tổng SL các dòng trong file: SKU1DEF không phải combo nên bằng nhau.

    def test_truncation_warning_row_when_over_limit(self, monkeypatch):
        from apps.inventory import exports_definitions

        _seed_flow()
        monkeypatch.setattr(exports_definitions, "_EXPORT_LIMIT", 0)
        writer = _write(self.definition, self._params())
        assert writer.rows[0][0].startswith("⚠️ Kỳ lọc có")
        assert "1 dòng" in writer.rows[0][0]
        assert "0 dòng đầu" in writer.rows[0][0]
        # Tổng cộng (SKU ghi sổ) vẫn tính TOÀN BỘ kỳ dù bị cắt bớt phần dòng chi tiết.
        assert writer.rows[-2][-1] == 5
        # Tổng SL các dòng trong file chỉ cộng dòng THẬT SỰ có trong file: đơn bị
        # cắt hết (limit=0) và dòng chờ khớp không có SKU nên không đóng góp gì.
        assert writer.rows[-1][-1] == 0


class TestDispatchHistoryExportWriteSku:
    def setup_method(self):
        self.definition = get_definition("dispatch_history")

    def _params(self, **overrides):
        return self.definition.validate(None, _raw_filters(group="sku", **overrides))

    def test_header_matches_view(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        assert writer.headers == [
            "SKU", "Tên", "Số đơn", "SL theo đơn", "SL phiếu kho", "SL chuyển kho",
            "SL xuất", "Trong đó từ combo",
        ]

    def test_row_and_footer_values(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        assert writer.rows[0][:4] == ["SKU1DEF", "Áo", 1, 5]
        # 5 dòng Tổng: 4 dòng ghi sổ cũ + 1 dòng mới "cộng đúng các dòng trong
        # file" (Task 2 — chế độ Theo SKU trước đây KHÔNG có dòng đối chiếu này,
        # trong khi đây chính là chế độ cần nó nhất vì MỌI dòng chi tiết đều đã nở).
        footer_labels = [row[0] for row in writer.rows[-5:]]
        assert footer_labels == [
            "Tổng theo đơn (SKU ghi sổ)", "Tổng phiếu xuất kho (SKU ghi sổ)",
            "Tổng phiếu chuyển kho (SKU ghi sổ)", "Tổng cộng (SKU ghi sổ)",
            "Tổng SL các dòng trong file",
        ]
        assert writer.rows[-2][-2] == 5  # Tổng cộng (SKU ghi sổ), cột "SL xuất"
        assert writer.rows[-1][-2] == 5  # Tổng SL các dòng trong file, cột "SL xuất"


# Cột "SL xuất/nhập" ở chế độ Theo SKU: dispatch có thêm cột đơn nên lệch 1 so với return.
_SKU_QTY_COL = {"dispatch_history": 6, "return_history": 5}


def _sku_rows(kind: str):
    definition = get_definition(kind)
    writer = _write(definition, definition.validate(None, _raw_filters(group="sku")))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    return writer, detail


@pytest.mark.parametrize(
    ("kind", "seed"),
    [("dispatch_history", _seed_combo_dispatch), ("return_history", _seed_return_combo)],
    ids=["dispatch", "return"],
)
def test_sku_export_merges_combo_into_components(kind, seed):
    """Dòng COMBO-X biến mất, SL cộng vào SP-A/SP-B; cột cuối tách phần từ combo —
    cùng luật cho cả nhánh xuất lẫn nhập hoàn."""
    seed()
    writer, detail = _sku_rows(kind)
    qty = _SKU_QTY_COL[kind]
    assert writer.headers[-1] == "Trong đó từ combo"
    assert [r[0] for r in detail] == ["SP-A", "SP-B"]
    assert [(r[qty], r[qty + 1]) for r in detail] == [(2, 2), (4, 4)]


@pytest.mark.parametrize(
    ("seed", "expected_qty", "expected_from_combo", "reason"),
    [
        # SP-A vừa bán lẻ (3) vừa nằm trong COMBO-X (2 combo ×1 = 2) → CHỈ MỘT dòng
        # SP-A; SL xuất = 3 + 2 = 5; "Trong đó từ combo" chỉ tính phần combo (2).
        pytest.param(
            _seed_retail_and_combo_dispatch, 5, 2,
            "SP-A bán lẻ và SP-A nở từ combo phải gộp CHUNG 1 dòng", id="retail_and_combo",
        ),
        # Hai combo khác nhau (COMBO-X = SP-A ×1, COMBO-Y = SP-A ×3) cùng chứa SP-A
        # cộng dồn vào CÙNG một dòng: 2×1 + 2×3 = 8, toàn bộ đến từ combo.
        pytest.param(
            _seed_two_combos_sharing_component_dispatch, 8, 8,
            "SP-A từ COMBO-X và COMBO-Y phải gộp CHUNG 1 dòng", id="two_combos",
        ),
    ],
)
def test_dispatch_sku_export_merges_same_component_into_one_row(
    seed, expected_qty, expected_from_combo, reason
):
    seed()
    _, detail = _sku_rows("dispatch_history")
    a_rows = [r for r in detail if r[0] == "SP-A"]
    assert len(a_rows) == 1, reason
    assert a_rows[0][6] == expected_qty
    assert a_rows[0][7] == expected_from_combo


@pytest.mark.parametrize(
    ("kind", "seed"),
    [("dispatch_history", _seed_combo_dispatch), ("return_history", _seed_return_combo)],
    ids=["dispatch", "return"],
)
def test_sku_export_adds_row_total_matching_line_sum(kind, seed):
    """Task 2/3: chế độ Theo SKU trước đây KHÔNG có dòng tổng đối chiếu — đây
    lại là chế độ cần nhất vì MỌI dòng chi tiết đều đã nở. Dòng mới phải cộng
    ĐÚNG cột "SL xuất/nhập" của các dòng chi tiết, và ở đây (có combo) phải LỚN
    HƠN "Tổng cộng (SKU ghi sổ)" vì combo nở ra nhiều sản phẩm con."""
    seed()
    writer, detail = _sku_rows(kind)
    total_row = next(r for r in writer.rows if r[0] == "Tổng SL các dòng trong file")
    ghi_so_row = next(r for r in writer.rows if r[0] == "Tổng cộng (SKU ghi sổ)")
    assert total_row[-2] == sum(r[_SKU_QTY_COL[kind]] for r in detail)
    assert total_row[-2] > ghi_so_row[-2], "combo phải làm dòng tổng mới LỚN HƠN dòng ghi sổ"


# ─────────────────────────── return_history: validate/estimate/file_name ──────


class TestReturnHistoryExportContract:
    def setup_method(self):
        self.definition = get_definition("return_history")

    def test_validate_rejects_bad_date(self):
        with pytest.raises(ValidationError):
            self.definition.validate(None, _raw_filters(date_to="not-a-date"))

class TestReturnHistoryExportWriteEntries:
    def setup_method(self):
        self.definition = get_definition("return_history")

    def _params(self, **overrides):
        return self.definition.validate(None, _raw_filters(**overrides))

    def test_header_matches_view(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        assert writer.headers == [
            "Thời gian", "Mã phiếu trả", "Mã hóa đơn nguồn", "Mã vận đơn",
            "Kênh KiotViet", "Kho", "Người thực hiện", "SKU", "Tên sản phẩm",
            "Thuộc combo", "Loại hoàn", "Ghi chú", "SL hoàn",
        ]

    def test_dedup_failed_scan_note_merged_into_entry_row(self):
        """Mirror `test_return_export_failed_dup_scan_note_merged_into_entry_row`:
        scan FAILED trùng mã vận đơn với phiếu trả đã ghi sổ phải KHÔNG lặp dòng,
        và note của scan phải gộp vào dòng phiếu (không mất — audit 2026-07-15b)."""
        channel = Channel.objects.create(code="lp-fhr-def", name="FHR-DEF")
        customer = Customer.objects.create(
            full_name="KR", phone_raw="0900000022", phone_normalized="0900000022"
        )
        wh = Warehouse.objects.create(code="WH-FHR-DEF", name="Kho R Def")
        p1 = Product.objects.create(sku="SKUR1DEF", name="Áo R")
        order = Order.objects.create(
            channel=channel, customer=customer, name="KR", phone="0900000022",
            order_code="DH0R1DEF", invoice_code="HD0R1DEF", shipping_code="VDR9DEF",
            total_amount="1000",
        )
        StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=p1.id, qty_delta=-2,
            movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
        )
        ReturnEntryService.create_and_post_entry(
            warehouse_id=wh.id,
            source_order_id=order.id,
            shipping_code="VDR9DEF",
            lines=[{"product_id": p1.id, "quantity": 1, "return_type": ReturnType.DELIVERY_RETURNED}],
        )
        PendingShipmentScan.objects.create(
            code="VDR9DEF", kind=PendingScanKind.RETURN, warehouse=wh,
            status=PendingScanStatus.FAILED, note="khách gửi lại kiện 2",
        )
        writer = _write(self.definition, self._params())
        vd_rows = [row for row in writer.rows if row[3] == "VDR9DEF"]
        assert len(vd_rows) == 1
        assert "khách gửi lại kiện 2" in (vd_rows[0][11] or "")

    def test_footer_totals(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        # 4 dòng Tổng: 3 dòng ghi sổ cũ (đổi nhãn) + 1 dòng mới cộng đúng các
        # dòng có trong file.
        footer_labels = [row[0] for row in writer.rows[-4:]]
        assert footer_labels == [
            "Tổng hoàn theo đơn (SKU ghi sổ)", "Tổng phiếu chuyển kho (SKU ghi sổ)",
            "Tổng cộng (SKU ghi sổ)", "Tổng SL các dòng trong file",
        ]
        assert writer.rows[-4][-1] == 2  # seed: return quantity = 2
        assert writer.rows[-2][-1] == 2  # Tổng cộng (SKU ghi sổ) = return(2) + transfer(0)
        assert writer.rows[-1][-1] == 2  # Tổng SL các dòng trong file: SKUR1DEF/SKU1DEF không phải combo nên bằng nhau.

    def test_truncation_warning_row_when_over_limit(self, monkeypatch):
        from apps.inventory import exports_definitions

        _seed_flow()
        monkeypatch.setattr(exports_definitions, "_EXPORT_LIMIT", 0)
        writer = _write(self.definition, self._params())
        assert writer.rows[0][0].startswith("⚠️ Kỳ lọc có")
        # Tổng cộng (SKU ghi sổ) vẫn tính TOÀN BỘ kỳ dù bị cắt bớt phần dòng chi tiết.
        assert writer.rows[-2][-1] == 2
        # Tổng SL các dòng trong file chỉ cộng dòng THẬT SỰ có trong file: đơn bị
        # cắt hết (limit=0) và dòng chờ khớp không có SKU nên không đóng góp gì.
        assert writer.rows[-1][-1] == 0


class TestReturnHistoryExportWriteSku:
    def setup_method(self):
        self.definition = get_definition("return_history")

    def _params(self, **overrides):
        return self.definition.validate(None, _raw_filters(group="sku", **overrides))

    def test_header_matches_view(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        assert writer.headers == [
            "SKU", "Tên", "Số phiếu", "SL hoàn", "SL chuyển kho", "SL nhập",
            "Trong đó từ combo",
        ]

    def test_row_and_footer_values(self):
        _seed_flow()
        writer = _write(self.definition, self._params())
        assert writer.rows[0][:4] == ["SKU1DEF", "Áo", 1, 2]
        # 4 dòng Tổng: 3 dòng ghi sổ cũ + 1 dòng mới "cộng đúng các dòng trong
        # file" (Task 2 — cùng lý do với nhánh dispatch: chế độ Theo SKU trước
        # đây không có dòng đối chiếu này dù đây là chế độ cần nó nhất).
        footer_labels = [row[0] for row in writer.rows[-4:]]
        assert footer_labels == [
            "Tổng hoàn theo đơn (SKU ghi sổ)", "Tổng phiếu chuyển kho (SKU ghi sổ)",
            "Tổng cộng (SKU ghi sổ)", "Tổng SL các dòng trong file",
        ]
        assert writer.rows[-2][-2] == 2  # Tổng cộng (SKU ghi sổ), cột "SL nhập"
        assert writer.rows[-1][-2] == 2  # Tổng SL các dòng trong file, cột "SL nhập"


# ─────────────── dispatch/return history: bộ lọc NHIỀU kho trong file ───────────


def _seed_flow_second_warehouse():
    """Kho thứ hai (đơn + mã chờ khớp riêng) — file export phải bám đúng tập kho
    đang chọn trên màn hình, không rộng hơn cũng không hẹp hơn."""
    channel = Channel.objects.get(code="lp-fh-def")
    customer = Customer.objects.get(phone_normalized="0900000011")
    wh2 = Warehouse.objects.create(code="WH-FH-DEF2", name="Kho B Def")
    p2 = Product.objects.create(sku="SKU2DEF", name="Quần")
    order2 = Order.objects.create(
        channel=channel,
        customer=customer,
        name="K",
        phone="0900000011",
        order_code="DH002DEF",
        invoice_code="HD002DEF",
        shipping_code="VD002DEF",
        total_amount="1000",
    )
    StockCommandService.record_movement(
        warehouse_id=wh2.id, product_id=p2.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order2.id,
    )
    PendingShipmentScan.objects.create(
        code="VDUNMATCHEDDEF2", kind=PendingScanKind.DISPATCH, warehouse=wh2,
        status=PendingScanStatus.PENDING,
    )
    return wh2, order2


class TestFlowExportMultiWarehouseFilter:
    def setup_method(self):
        self.definition = get_definition("dispatch_history")

    def _write_with(self, **raw):
        return _write(self.definition, self.definition.validate(None, _raw_filters(**raw)))

    def test_file_covers_every_selected_warehouse(self):
        wh, order, _p1, _entry = _seed_flow()
        wh2, order2 = _seed_flow_second_warehouse()

        writer = self._write_with(warehouse_ids=f"{wh.id},{wh2.id}")

        codes = {row[2] for row in writer.rows}
        assert {order.shipping_code, order2.shipping_code} <= codes
        # Mã chờ khớp của CẢ HAI kho cũng phải có mặt (khối pending dùng chung bộ lọc).
        assert {"VDUNMATCHEDDEF", "VDUNMATCHEDDEF2"} <= codes
        total_row = next(r for r in writer.rows if r[0] == "Tổng theo đơn (SKU ghi sổ)")
        assert total_row[11] == 8

    def test_file_excludes_warehouse_not_selected(self):
        wh, order, _p1, _entry = _seed_flow()
        _wh2, order2 = _seed_flow_second_warehouse()

        writer = self._write_with(warehouse_ids=str(wh.id))

        codes = {row[2] for row in writer.rows}
        assert order.shipping_code in codes
        assert order2.shipping_code not in codes
        assert "VDUNMATCHEDDEF2" not in codes
        total_row = next(r for r in writer.rows if r[0] == "Tổng theo đơn (SKU ghi sổ)")
        assert total_row[11] == 5

    def test_legacy_single_warehouse_id_job_still_filters(self):
        """Job cũ còn nằm trong hàng đợi vẫn gửi `warehouse_id` đơn lẻ."""
        wh, order, _p1, _entry = _seed_flow()
        _wh2, order2 = _seed_flow_second_warehouse()

        writer = self._write_with(warehouse_id=wh.id)

        codes = {row[2] for row in writer.rows}
        assert order.shipping_code in codes
        assert order2.shipping_code not in codes

    def test_sku_branch_covers_every_selected_warehouse(self):
        wh, _order, _p1, _entry = _seed_flow()
        wh2, _order2 = _seed_flow_second_warehouse()

        writer = self._write_with(warehouse_ids=f"{wh.id},{wh2.id}", group="sku")

        skus = {row[0] for row in writer.rows}
        assert {"SKU1DEF", "SKU2DEF"} <= skus


# ─────────────── dispatch_history: tách dòng theo mã hàng (chế độ Theo đơn) ─────


def test_dispatch_export_splits_one_row_per_sku():
    """Đơn 2 mã hàng → 2 dòng, mỗi dòng mang SL của riêng mã đó."""
    _seed_two_skus()
    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))

    assert writer.headers == [
        "Thời gian", "Mã hóa đơn", "Mã vận đơn", "Kênh KiotViet", "Kho",
        "Người thực hiện", "SKU", "Tên sản phẩm", "Thuộc combo", "Ghi chú",
        "Số SKU", "SL xuất",
    ]
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[6], r[11]) for r in detail] == [("SP-A", 2), ("SP-B", 1)]
    # Các cột đầu lặp nguyên giá trị để lọc/pivot được trên Excel.
    assert detail[0][1] == detail[1][1]


def test_dispatch_export_expands_combo_rows():
    """2 combo, combo gồm A×1 và B×2 → A = 2, B = 4; cột Thuộc combo ghi SKU gốc."""
    _seed_combo_dispatch()
    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[6], r[8], r[11]) for r in detail] == [
        ("SP-A", "COMBO-X", 2),
        ("SP-B", "COMBO-X", 4),
    ]


def test_dispatch_export_khong_lap_dong_khi_combo_da_chuyen_so():
    """Combo đã chuyển sổ sang mã lẻ chỉ ra ĐÚNG 1 dòng cho mỗi mã lẻ.

    Ca prod SPXVN066980553678 (20/08): sổ có cả bút toán combo (đã đảo bằng
    "Chuyển sổ combo sang mã lẻ") lẫn bút toán của 2 mã lẻ. Nếu dòng combo còn
    sót lại, file nở nó ra chính 2 mã lẻ đó — mỗi mã hiện hai dòng, dòng "Tổng
    SL các dòng trong file" cũng gấp đôi.
    """
    wh, order, combo = _seed_combo("MOVED")
    a = Product.objects.get(sku="SP-A")
    b = Product.objects.get(sku="SP-B")
    ref = {"reference_type": "order", "reference_id": order.id}
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=-1,
        movement_type=MovementType.SALE, **ref,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=combo.id, qty_delta=1,
        movement_type=MovementType.SALE_REVERSAL,
        note=COMBO_BACKFILL_RELEASE_NOTE, **ref,
    )
    for product, qty in ((a, 1), (b, 2)):
        StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=product.id, qty_delta=-qty,
            movement_type=MovementType.SALE, note=COMBO_BACKFILL_ISSUE_NOTE, **ref,
        )

    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))

    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[6], r[8], r[11]) for r in detail] == [("SP-B", "", 2), ("SP-A", "", 1)]
    # Cột "Số SKU" đếm 2 mã lẻ, không tính SKU combo đã chuyển sổ.
    assert {r[10] for r in detail} == {2}
    assert writer.rows[-1][0] == "Tổng SL các dòng trong file"
    assert writer.rows[-1][11] == 3


def test_dispatch_export_document_row_splits_by_sku():
    """Phiếu xuất kho (kind='document') nhiều mã hàng cũng tách 1 dòng/mã hàng —
    `lines` của phiếu KHÔNG có key `gross` (khác dòng đơn), pipeline `_emit_lines`
    dùng chung cho cả ba loại dòng vẫn phải xử lý đúng (nợ review Task 4)."""
    wh = Warehouse.objects.create(code="WH-DOC-SPLIT", name="Kho Doc Split")
    a = Product.objects.create(sku="SP-DOC-A", name="Sản phẩm Doc A")
    b = Product.objects.create(sku="SP-DOC-B", name="Sản phẩm Doc B")
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=wh.id,
        lines=[{"product_id": a.id, "quantity": 3}, {"product_id": b.id, "quantity": 5}],
        note="",
        user_id=None,
    )
    StockDocumentService.submit_for_approval(document_id=doc.id, user_id=None)
    StockDocumentService.approve_document(document_id=doc.id, user_id=None)

    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    doc_rows = [r for r in detail if str(r[1]).startswith("Phiếu xuất kho")]
    # `issue_document_history.enrich_docs` sắp dòng theo -qty rồi sku → B(5) trước A(3).
    assert [(r[6], r[11]) for r in doc_rows] == [("SP-DOC-B", 5), ("SP-DOC-A", 3)]


def test_dispatch_export_adds_expanded_total_row():
    """Footer ghi sổ giữ nguyên; thêm dòng tổng cộng đúng các dòng có trong file."""
    _seed_combo_dispatch()
    definition = get_definition("dispatch_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    last = writer.rows[-1]
    assert last[0] == "Tổng SL các dòng trong file"
    assert last[11] == sum(r[11] for r in detail)
    labels = [r[0] for r in writer.rows if str(r[0]).startswith("Tổng")]
    assert labels[:4] == [
        "Tổng theo đơn (SKU ghi sổ)",
        "Tổng phiếu xuất kho (SKU ghi sổ)",
        "Tổng phiếu chuyển kho (SKU ghi sổ)",
        "Tổng cộng (SKU ghi sổ)",
    ]


# ─────────────── return_history: tách dòng theo mã hàng (chế độ Theo đơn) ──────


def test_return_export_splits_one_row_per_sku():
    _seed_return_two_skus()
    definition = get_definition("return_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    assert writer.headers == [
        "Thời gian", "Mã phiếu trả", "Mã hóa đơn nguồn", "Mã vận đơn",
        "Kênh KiotViet", "Kho", "Người thực hiện", "SKU", "Tên sản phẩm",
        "Thuộc combo", "Loại hoàn", "Ghi chú", "SL hoàn",
    ]
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[7], r[12]) for r in detail] == [("SP-A", 2), ("SP-B", 1)]


def test_return_export_expands_combo_rows():
    _seed_return_combo()
    definition = get_definition("return_history")
    writer = _write(definition, definition.validate(None, _raw_filters()))
    detail = [r for r in writer.rows if not str(r[0]).startswith("Tổng")]
    assert [(r[7], r[9], r[12]) for r in detail] == [
        ("SP-A", "COMBO-X", 2),
        ("SP-B", "COMBO-X", 4),
    ]
    assert writer.rows[-1][0] == "Tổng SL các dòng trong file"
    assert writer.rows[-1][12] == sum(r[12] for r in detail)


# ─────────────── cột "Kênh KiotViet" (kiot của đơn) ở chế độ Theo đơn ──────────


def _seed_flow_with_kiot(name: str = "Kiot Miền Bắc"):
    """`_seed_flow` + gắn kênh KiotViet cho đơn.

    Kiot là thuộc tính của ĐƠN chứ không phải của bút toán kho, nên cả tab Xuất
    (đơn xuất) lẫn tab Nhập hoàn (đơn NGUỒN của phiếu trả) phải đọc ra cùng một
    tên — chế độ Theo SKU gộp nhiều đơn của nhiều kiot nên KHÔNG có cột này."""
    from apps.kiotviet.models import KiotVietChannel

    wh, order, p1, entry = _seed_flow()
    order.kiotviet_channel = KiotVietChannel.objects.create(
        name=name, retailer="retailer-kiot-def"
    )
    order.save(update_fields=["kiotviet_channel"])
    return wh, order, p1, entry


def test_dispatch_export_shows_kiot_of_order():
    _wh, order, _p1, _entry = _seed_flow_with_kiot()
    definition = get_definition("dispatch_history")

    writer = _write(definition, definition.validate(None, _raw_filters()))

    order_rows = [r for r in writer.rows if r[2] == order.shipping_code]
    assert len(order_rows) == 1
    assert order_rows[0][3] == "Kiot Miền Bắc"


def test_return_export_shows_kiot_of_source_order():
    _wh, order, _p1, _entry = _seed_flow_with_kiot()
    definition = get_definition("return_history")

    writer = _write(definition, definition.validate(None, _raw_filters()))

    entry_rows = [r for r in writer.rows if r[2] == order.invoice_code]
    assert len(entry_rows) == 1
    assert entry_rows[0][4] == "Kiot Miền Bắc"


def test_dispatch_export_leaves_kiot_blank_for_rows_without_order():
    """Phiếu xuất kho và mã chờ khớp không thuộc đơn nào — ô Kiot phải TRỐNG,
    không được mượn kiot của dòng đơn đứng cạnh."""
    _seed_flow_with_kiot()
    wh = Warehouse.objects.create(code="WH-DOC-KIOT", name="Kho Doc Kiot")
    product = Product.objects.create(sku="SP-DOC-KIOT", name="Sản phẩm Doc Kiot")
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=wh.id,
        lines=[{"product_id": product.id, "quantity": 3}],
        note="",
        user_id=None,
    )
    StockDocumentService.submit_for_approval(document_id=doc.id, user_id=None)
    StockDocumentService.approve_document(document_id=doc.id, user_id=None)
    definition = get_definition("dispatch_history")

    writer = _write(definition, definition.validate(None, _raw_filters()))

    doc_rows = [r for r in writer.rows if str(r[1]).startswith("Phiếu xuất kho")]
    pending_rows = [r for r in writer.rows if r[2] == "VDUNMATCHEDDEF"]
    assert doc_rows and pending_rows
    assert {r[3] for r in doc_rows + pending_rows} == {""}


class TestDispatchHistoryExportScannedCode:
    """Ca prod 84859795173645 (16/09/2026): kho quét vận đơn MỚI của kiện bảo hành
    gửi lại rồi khớp tay vào đơn cũ. Mã quét phải tra được trong file Excel và
    ghi chú lúc quét phải nằm trên dòng đơn — không thì file nói "không xuất"."""

    def setup_method(self):
        self.definition = get_definition("dispatch_history")

    def _params(self, **overrides):
        return self.definition.validate(None, _raw_filters(**overrides))

    def test_shipping_code_cell_carries_scanned_code_when_it_differs(self):
        wh, order, _p1, _entry = _seed_flow()
        PendingShipmentScan.objects.create(
            code="84859795173645", kind=PendingScanKind.DISPATCH, warehouse=wh,
            status=PendingScanStatus.MATCHED, matched_order=order,
            note="trả bảo hành DC12 — đổi mới",
        )
        writer = _write(self.definition, self._params())
        hd_rows = [row for row in writer.rows if row[1] == order.invoice_code]
        assert len(hd_rows) == 1
        assert hd_rows[0][2] == "VD001DEF; 84859795173645"
        assert hd_rows[0][9] == "trả bảo hành DC12 — đổi mới"
        # Mã đã khớp không được lặp thêm một dòng "chờ khớp" riêng.
        assert not any(row[2] == "84859795173645" for row in writer.rows)

    def test_scanned_code_row_is_found_by_order_search(self):
        wh, order, _p1, _entry = _seed_flow()
        PendingShipmentScan.objects.create(
            code="84859797173814", kind=PendingScanKind.DISPATCH, warehouse=wh,
            status=PendingScanStatus.MATCHED, matched_order=order,
        )
        writer = _write(self.definition, self._params(order="84859797173814"))
        hd_rows = [row for row in writer.rows if row[1] == order.invoice_code]
        assert len(hd_rows) == 1
        assert hd_rows[0][2] == "VD001DEF; 84859797173814"
