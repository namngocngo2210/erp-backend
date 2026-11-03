"""Test `ExportDefinition` "warranty_claims" của app warranty — Task 16 của dự án
"xuất file chạy nền".

Mirror `claim_views.export_warranty_claims_view` gốc (view đã xoá ở Task 18):
cùng 2 nhánh (ids đã chọn / filter q+status), cùng bộ cột `claim_excel.
_HEADERS`. Khác biệt DUY NHẤT so với view cũ: view cũ KHÔNG có trần nào —
`_CLAIMS_MAX_ROWS = 20000` là trần MỚI (xem docstring `exports_definitions.py`).

Test ở đây gọi thẳng `definition.write(params, writer, progress)` với một writer
giả gom giá trị thô — cùng kỹ thuật `test_inventory_export_definitions.py`
(Task 15), lớp writer thật đã có test riêng ở `apps/exports/tests/test_writers.py`.
"""
from __future__ import annotations

import pytest
from rest_framework.exceptions import ValidationError

from apps.exports.application.registry import get_definition
from apps.warranty.claim_excel import _HEADERS
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus

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
    assert progressed == sorted(progressed)
    # progress() phải được gọi ÍT NHẤT 1 lần kể cả khi 0 dòng khớp filter (fix
    # round 1: write() nay gọi progress(count) không điều kiện sau vòng lặp) —
    # không còn cần né bằng `if writer.rows:` như trước.
    assert progressed[-1] == len(writer.rows)
    return writer


# ─────────────────────────── đăng ký + validate + quyền ───────────────────────────


def test_validate_defaults():
    definition = get_definition("warranty_claims")
    params = definition.validate(None, {})
    assert params == {"ids": [], "q": "", "status": None}


def test_validate_parses_ids_and_status():
    definition = get_definition("warranty_claims")
    params = definition.validate(None, {"ids": ["1", "2"], "status": str(int(WarrantyClaimStatus.COMPLETED))})
    assert params["ids"] == [1, 2]
    assert params["status"] == int(WarrantyClaimStatus.COMPLETED)


def test_validate_rejects_non_numeric_ids_with_400_not_500():
    """Phần D (Task 18): `ids: ["abc"]` phải raise ValidationError (→ 400),
    KHÔNG được để ValueError trần lọt ra ngoài (→ 500). Mirror bug/fix tương ứng
    ở OrdersExport.validate (apps/orders/exports_definitions.py)."""
    definition = get_definition("warranty_claims")
    with pytest.raises(ValidationError):
        definition.validate(None, {"ids": ["abc"]})


# ─────────────────────────── estimate(): trần mới `_CLAIMS_MAX_ROWS` ──────────────


def test_estimate_ignores_cap_when_ids_selected(monkeypatch, order_factory):
    """Export theo lựa chọn (ids) không bị chặn trần — mirror OrdersExport: người
    dùng đã tự giới hạn tập bằng thao tác chọn dòng trên UI."""
    from apps.warranty import exports_definitions

    order = order_factory(shipping_code="VD-EST-IDS")
    claim = WarrantyClaim.objects.create(order=order)
    definition = get_definition("warranty_claims")
    params = definition.validate(None, {"ids": [claim.id]})
    monkeypatch.setattr(exports_definitions, "_CLAIMS_MAX_ROWS", 0)
    assert definition.estimate(params) == 1


def test_estimate_rejects_over_cap(monkeypatch, order_factory):
    """Bám snippet brief Step 1, có seed 1 claim thật: `estimate()` so
    `matched > _CLAIMS_MAX_ROWS`; không seed gì thì `matched=0 > cap=0` là False,
    KHÔNG raise (đúng ngữ nghĩa gốc, cùng lưu ý test_inventory_export_definitions.
    py::test_stock_rejects_over_cap) — phải có ≥1 phiếu để vượt trần thật sự đúng nghĩa."""
    from apps.warranty import exports_definitions

    order = order_factory(shipping_code="VD-EST-CAP")
    WarrantyClaim.objects.create(order=order)

    definition = get_definition("warranty_claims")
    params = definition.validate(None, {})
    monkeypatch.setattr(exports_definitions, "_CLAIMS_MAX_ROWS", 0)
    with pytest.raises(ValidationError) as excinfo:
        definition.estimate(params)
    assert "vượt giới hạn 0 phiếu mỗi lần xuất" in str(excinfo.value)


# ─────────────────────────── file_name ───────────────────────────


# ─────────────────────────── write(): 2 nhánh ───────────────────────────


def test_write_header_matches_claim_excel():
    definition = get_definition("warranty_claims")
    params = definition.validate(None, {})
    writer = _write(definition, params)
    assert writer.headers == list(_HEADERS)


def test_write_by_selected_ids(order_factory):
    definition = get_definition("warranty_claims")
    order1 = order_factory(shipping_code="VD-W-SEL-1")
    claim1 = WarrantyClaim.objects.create(order=order1)
    order2 = order_factory(shipping_code="VD-W-SEL-2")
    WarrantyClaim.objects.create(order=order2)

    params = definition.validate(None, {"ids": [claim1.id]})
    writer = _write(definition, params)
    assert len(writer.rows) == 1
    assert writer.rows[0][4] == "VD-W-SEL-1"


def test_write_row_content_matches_order_and_claim_fields(order_factory):
    """Mirror `test_export_returns_xlsx_with_expected_columns` +
    `test_export_includes_warranty_period_columns` (test_claim_bulk_export.py, đã
    xoá ở Task 18): `_claim_row` phải trả đúng NỘI DUNG từng cột, không chỉ đúng
    header — journeys_count tự sinh (seq=1) khi tạo claim, ngày kích hoạt/hết hạn
    format dd/mm/YYYY, hết hạn = kích hoạt + 24 tháng."""
    from datetime import date

    from apps.warranty.claim_service import WarrantyClaimService

    order = order_factory(
        name="Khách A",
        phone="0900000099",
        address="123 Đường ABC",
        shipping_code="VD-EXPORT-1",
        invoice_code="HD-EXPORT-1",
    )
    claim = WarrantyClaimService.create_claim(
        order_id=order.id, data={"activation_date": date(2026, 1, 15)}, created_by=None
    )

    definition = get_definition("warranty_claims")
    params = definition.validate(None, {})
    writer = _write(definition, params)
    row = writer.rows[0]
    assert row[0] == "HD-EXPORT-1"
    assert row[1] == "Khách A"
    assert row[2] == "0900000099"
    assert row[3] == "123 Đường ABC"
    assert row[4] == "VD-EXPORT-1"
    assert row[5] == "Đợi khách XN"
    assert row[6] == 1  # journey seq=1 tự sinh khi tạo claim
    assert row[8] == "15/01/2026"  # Ngày kích hoạt
    assert row[9] == "15/01/2028"  # Ngày hết hạn (+24 tháng)
    claim.refresh_from_db()
    assert row[10] == claim.warranty_days_left  # Số ngày bảo hành còn lại


def test_write_row_expired_days_left_shows_het_han(order_factory):
    """Mirror `test_export_expired_days_left_shows_het_han`: days_left <= 0 ->
    'Hết hạn' (khớp web warranty-claim-columns.ts) — logic riêng của
    `claim_excel._fmt_days_left`, không phải mechanism chung của writer nên phải
    khoá riêng ở tầng definition."""
    from datetime import date

    from apps.warranty.claim_service import WarrantyClaimService

    order = order_factory(shipping_code="VD-EXPIRED")
    WarrantyClaimService.create_claim(
        order_id=order.id, data={"activation_date": date(2020, 1, 1)}, created_by=None
    )

    definition = get_definition("warranty_claims")
    params = definition.validate(None, {})
    writer = _write(definition, params)
    assert writer.rows[0][10] == "Hết hạn"


def test_write_filters_by_status(order_factory):
    definition = get_definition("warranty_claims")
    done_order = order_factory(shipping_code="VD-W-DONE")
    WarrantyClaim.objects.create(order=done_order, status=WarrantyClaimStatus.COMPLETED)
    other_order = order_factory(shipping_code="VD-W-OTHER")
    WarrantyClaim.objects.create(order=other_order, status=WarrantyClaimStatus.WAITING_CUSTOMER_CONFIRM)

    params = definition.validate(None, {"status": int(WarrantyClaimStatus.COMPLETED)})
    writer = _write(definition, params)
    assert len(writer.rows) == 1
    assert writer.rows[0][4] == "VD-W-DONE"


def test_write_truncates_and_warns_when_cap_exceeded_after_job_created(monkeypatch, order_factory):
    """TOCTOU do kiến trúc async sinh ra (cùng lưu ý test_inventory_export_
    definitions.py::test_write_truncates_and_warns_when_cap_exceeded_after_job_
    created): `estimate()` đếm 1 LẦN, đồng bộ, lúc tạo job; `write()` chạy sau đó
    ở worker, có thể trễ — số phiếu khớp filter có thể phình vượt trần GIỮA hai
    mốc đó. Test này KHÔNG gọi `estimate()` trước — mô phỏng đúng ca "trần đã đổi
    sau khi job được tạo": `write()` phải tự áp lại trần và cảnh báo thay vì kéo
    hết dữ liệu vào RAM hay raise (raise ở write() bị `tasks.run_export` nuốt mất
    con số cụ thể — xem docstring `exports_definitions.py`)."""
    from apps.warranty import exports_definitions

    monkeypatch.setattr(exports_definitions, "_CLAIMS_MAX_ROWS", 2)
    for i in range(3):
        order = order_factory(shipping_code=f"VD-W-CAP-{i}")
        WarrantyClaim.objects.create(order=order)

    definition = get_definition("warranty_claims")
    params = definition.validate(None, {})
    writer = _write(definition, params)

    data_rows = [r for r in writer.rows if str(r[4]).startswith("VD-W-CAP-")]
    assert len(data_rows) == 2, "write() phải tự cắt về đúng trần, không kéo cả 3 dòng"
    warning_rows = [r for r in writer.rows if str(r[0]).startswith("⚠️")]
    assert len(warning_rows) == 1, "phải có đúng 1 dòng cảnh báo, không raise lỗi"
    assert "3 phiếu" in warning_rows[0][0]
    assert "2 phiếu đầu" in warning_rows[0][0]
