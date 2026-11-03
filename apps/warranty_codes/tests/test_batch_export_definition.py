"""Test `ExportDefinition` "warranty_code_batch" của app warranty_codes — Task 16
của dự án "xuất file chạy nền".

Mirror `batch_views.export_batch` + `export_service.export_batch` gốc (cả hai
đã xoá ở Task 18 cùng endpoint đồng bộ, xem
docs/.superpowers/sdd/2026-08-06-async-export-jobs/task-18-report.md): cùng
header `("SERIAL", "URL", "SKU")`, cùng vòng `.iterator(chunk_size=5000)`, cùng
việc set `exported_at` SAU KHI ghi xong.
"""
from __future__ import annotations

import pytest
from rest_framework.exceptions import ValidationError

from apps.exports.application.registry import get_definition
from apps.catalog.models import Product
from apps.sysconfig import override_sysconfig
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import WarrantyCodeBatch

pytestmark = pytest.mark.django_db


class _RecordingWriter:
    def __init__(self):
        self.headers: list = []
        self.rows: list[list] = []

    def write_header(self, headers):
        self.headers = list(headers)

    def write_row(self, values):
        self.rows.append(list(values))


def test_validate_requires_batch_id():
    definition = get_definition("warranty_code_batch")
    with pytest.raises(ValidationError):
        definition.validate(None, {})


def test_validate_rejects_unknown_batch():
    definition = get_definition("warranty_code_batch")
    with pytest.raises(ValidationError):
        definition.validate(None, {"batch_id": 999999})


def test_validate_rejects_batch_still_generating(settings):
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 0
    batch = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    with pytest.raises(ValidationError, match="đang sinh"):
        definition.validate(None, {"batch_id": batch.id})


def test_file_name_uses_batch_code():
    batch = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})
    assert definition.file_name(params) == f"{batch.batch_code}.xlsx"


def test_estimate_counts_codes_in_batch():
    batch = WarrantyCodeBatchService.create_batch(quantity=5, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})
    assert definition.estimate(params) == 5


def test_write_emits_all_codes_and_marks_exported():
    """Snippet gốc brief Step 4 — giữ nguyên."""
    batch = WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})

    rows = []

    class _W:
        def write_header(self, headers):
            rows.append(tuple(headers))

        def write_row(self, values):
            rows.append(tuple(values))

    definition.write(params, _W(), lambda n: None)
    assert rows[0] == ("SERIAL", "CODE", "URL", "SKU")
    assert len(rows) == 4
    batch.refresh_from_db()
    assert batch.exported_at is not None


def test_write_header_and_row_count_match_batch_quantity():
    batch = WarrantyCodeBatchService.create_batch(quantity=4, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})

    writer = _RecordingWriter()
    progressed: list[int] = []
    definition.write(params, writer, progressed.append)

    assert writer.headers == ["SERIAL", "CODE", "URL", "SKU"]
    assert len(writer.rows) == 4
    assert progressed == sorted(progressed)
    assert progressed[-1] == len(writer.rows)


def test_write_row_content_matches_serial_and_url_with_empty_sku_when_no_product():
    """Mirror `test_export_batch_excel_content` (test_export.py, đã xoá ở Task
    18): lô CHƯA gắn sản phẩm (reserved) -> cột SKU rỗng, cột SERIAL/URL đúng mã
    thật của batch (không phải chỉ đúng SỐ LƯỢNG dòng như test_write_emits_all_
    codes_and_marks_exported)."""
    from apps.sysconfig import sysconfig

    batch = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})

    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)

    first_code = WarrantyCodeBatch.objects.get(id=batch.id).codes.order_by("seq").first()
    assert writer.rows[0] == [
        first_code.serial,
        first_code.code,
        f"{sysconfig.get('WARRANTY_QR_BASE_URL')}{first_code.code}",
        "",
    ]


def test_write_row_carries_sku_of_assigned_product():
    from apps.sysconfig import sysconfig

    product = Product.objects.create(name="SP Bảo hành", sku="SKU-WCB-1")
    batch = WarrantyCodeBatchService.create_batch(quantity=2, product_id=product.id, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})

    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)

    assert all(row[3] == "SKU-WCB-1" for row in writer.rows)
    assert all(row[2].startswith(sysconfig.get("WARRANTY_QR_BASE_URL")) for row in writer.rows)
    # Cột CODE (18/09/2026): mã 8 ký tự in rời để xưởng in không phải bóc từ URL.
    assert all(row[2] == f"{sysconfig.get('WARRANTY_QR_BASE_URL')}{row[1]}" for row in writer.rows)


def test_write_normalizes_qr_base_url_missing_trailing_slash():
    """Operator gõ tay WARRANTY_QR_BASE_URL trong cài đặt hệ thống thiếu dấu `/`
    cuối vẫn phải ra URL hợp lệ — tem đã in sai thì không sửa lại được, xem
    `apps/warranty_codes/exports_definitions.py` (dòng gán `base_url`)."""
    batch = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})

    with override_sysconfig(WARRANTY_QR_BASE_URL="https://delax.com.vn/sp"):
        writer = _RecordingWriter()
        definition.write(params, writer, lambda n: None)

    first_code = WarrantyCodeBatch.objects.get(id=batch.id).codes.order_by("seq").first()
    assert writer.rows[0][2] == f"https://delax.com.vn/sp/{first_code.code}"


def test_write_sets_exported_at_only_after_write_completes():
    """`exported_at` set SAU KHI ghi xong (không phải trước): giữ nguyên hành vi
    view đồng bộ — trước khi write() chạy, batch chưa được coi là đã xuất."""
    batch = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})
    assert batch.exported_at is None

    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)

    batch.refresh_from_db()
    assert batch.exported_at is not None


class _RaisesOnSecondRow:
    """Writer giả NÉM LỖI ở dòng dữ liệu thứ 2 — mô phỏng ghi lỗi giữa chừng
    (đĩa đầy, worker bị kill, ...). Dùng để khoá bất biến "exported_at chỉ set
    khi ghi TRỌN VẸN" thật sự: nếu ai dời `update(exported_at=...)` lên ĐẦU
    `write()` (trước vòng lặp) thay vì cuối, test này phải bắt được — so với
    bản trước chỉ so trước/sau TOÀN BỘ write() (không raise giữa chừng thì kể
    cả đặt exported_at ở đầu vẫn xanh, không khoá được gì)."""

    def __init__(self):
        self.headers: list = []
        self.rows: list[list] = []

    def write_header(self, headers):
        self.headers = list(headers)

    def write_row(self, values):
        if len(self.rows) == 1:
            raise RuntimeError("ghi lỗi giữa chừng (mô phỏng đĩa đầy / worker bị kill)")
        self.rows.append(list(values))


def test_write_does_not_mark_exported_when_write_fails_midway():
    """Đối chứng cho `test_write_sets_exported_at_only_after_write_completes`:
    write() ném lỗi ở dòng thứ 2 (còn 1 dòng chưa ghi trong lô 3 dòng) —
    `exported_at` PHẢI vẫn là None, vì lệnh `update(exported_at=...)` nằm ở
    CUỐI write(), sau vòng lặp ghi hết mọi dòng."""
    batch = WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})

    writer = _RaisesOnSecondRow()
    with pytest.raises(RuntimeError):
        definition.write(params, writer, lambda n: None)

    batch.refresh_from_db()
    assert batch.exported_at is None


# ── Xuất nhiều lô: `batch_ids` hoặc `filters` (17/09/2026) ──


def _params_of(definition, raw):
    return definition.validate(None, raw)


def test_validate_batch_ids_resolves_sorted_and_dedupes():
    b1 = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    b2 = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = _params_of(definition, {"batch_ids": [b2.id, b1.id, b2.id]})
    assert params["batch_ids"] == sorted([b1.id, b2.id])


def test_validate_batch_ids_rejects_unknown_or_malformed():
    definition = get_definition("warranty_code_batch")
    with pytest.raises(ValidationError):
        _params_of(definition, {"batch_ids": [999999]})
    with pytest.raises(ValidationError):
        _params_of(definition, {"batch_ids": ["abc"]})
    with pytest.raises(ValidationError):
        _params_of(definition, {"batch_ids": "1"})


def test_validate_filters_resolves_batches_like_list_endpoint():
    product = Product.objects.create(name="SP lọc", sku="SKU-WCB-F")
    assigned = WarrantyCodeBatchService.create_batch(quantity=1, product_id=product.id, created_by=None)
    WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    assert _params_of(definition, {"filters": {"status": "assigned"}})["batch_ids"] == [assigned.id]
    assert _params_of(definition, {"filters": {"product_id": product.id}})["batch_ids"] == [assigned.id]
    assert len(_params_of(definition, {"filters": {}})["batch_ids"]) == 2
    assert len(_params_of(definition, {"filters": {"q": "LOT-"}})["batch_ids"]) == 2
    with pytest.raises(ValidationError, match="Không có lô"):
        _params_of(definition, {"filters": {"q": "KHONG-CO"}})
    with pytest.raises(ValidationError):
        _params_of(definition, {"filters": {"date_from": "not-a-date"}})
    with pytest.raises(ValidationError):
        _params_of(definition, {"filters": "abc"})


def test_validate_rejects_when_any_selected_batch_is_generating(settings):
    ok = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    settings.WARRANTY_CODE_SYNC_GENERATE_MAX = 0
    busy = WarrantyCodeBatchService.create_batch(quantity=2, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    with pytest.raises(ValidationError) as exc:
        _params_of(definition, {"batch_ids": [ok.id, busy.id]})
    assert busy.batch_code in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        _params_of(definition, {"filters": {}})
    assert busy.batch_code in str(exc.value)


def test_file_name_single_vs_many():
    b1 = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    b2 = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    assert definition.file_name(_params_of(definition, {"batch_ids": [b1.id]})) == f"{b1.batch_code}.xlsx"
    many = definition.file_name(_params_of(definition, {"batch_ids": [b1.id, b2.id]}))
    assert many.startswith("ma-bao-hanh-2-lo-") and many.endswith(".xlsx")


def test_write_many_batches_orders_by_serial_with_per_batch_sku_and_marks_all_exported():
    product = Product.objects.create(name="SP nhiều lô", sku="SKU-WCB-M")
    later = WarrantyCodeBatchService.create_batch(quantity=2, start_seq=500, product_id=product.id, created_by=None)
    earlier = WarrantyCodeBatchService.create_batch(quantity=2, start_seq=100, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = _params_of(definition, {"batch_ids": [later.id, earlier.id]})
    assert definition.estimate(params) == 4

    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)
    assert writer.headers == ["SERIAL", "CODE", "URL", "SKU"]
    assert [r[0] for r in writer.rows] == ["DX-000100", "DX-000101", "DX-000500", "DX-000501"]
    assert [r[3] for r in writer.rows] == ["", "", "SKU-WCB-M", "SKU-WCB-M"]
    later.refresh_from_db()
    earlier.refresh_from_db()
    assert later.exported_at is not None and earlier.exported_at is not None
