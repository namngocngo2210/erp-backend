import io
from pathlib import Path

import pytest

from apps.contact_flow.application.services.import_service import (
    FlowImportError, FlowImportService,
)
from apps.contact_flow.care_stages import SEEDED_CARE_STAGE_NAMES
from apps.contact_flow.models import OperationResult, OperationStage, OperationTransition

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_seeded_care_stages():
    """Migration 0002 seed sẵn 5 bước chăm sóc vào MỌI database test.

    Các test dưới đếm tuyệt đối (34 bước) trên tiền đề "import vào cấu hình rỗng", nên
    xoá seed đi để con số vẫn nói đúng điều nó muốn nói. Workbook fixture cũng có bước
    tên "HDSD" — giữ seed lại thì `created` tụt còn 33 vì dòng đó thành upsert.
    """
    OperationStage.objects.filter(name__in=SEEDED_CARE_STAGE_NAMES).delete()
FIXTURE = Path(__file__).parent / "fixtures" / "ImportTacNghiep.xlsx"


def test_import_real_file_counts():
    with FIXTURE.open("rb") as f:
        report = FlowImportService.import_workbook(f)
    assert OperationStage.objects.count() == 34
    assert OperationResult.objects.count() == 12
    assert OperationTransition.objects.count() == 131
    assert report["stages"]["created"] == 34
    assert OperationStage.objects.filter(is_start=True).count() == 1


def test_import_twice_is_upsert_not_duplicate():
    for _ in range(2):
        with FIXTURE.open("rb") as f:
            report = FlowImportService.import_workbook(f)
    assert OperationStage.objects.count() == 34
    assert report["stages"]["updated"] == 34 and report["stages"]["created"] == 0


def test_parse_delay():
    assert FlowImportService.parse_delay("30 phút") == 30
    assert FlowImportService.parse_delay("1 giờ") == 60
    assert FlowImportService.parse_delay("10 ngày") == 14400
    assert FlowImportService.parse_delay("") is None
    with pytest.raises(ValueError):
        FlowImportService.parse_delay("mai gọi")


def test_bad_rule_reference_atomic(tmp_path):
    # Sửa 1 ô rule trỏ tới bước không tồn tại → lỗi dòng + KHÔNG ghi gì.
    import openpyxl
    wb = openpyxl.load_workbook(FIXTURE)
    wb["tacnghiepcauhinh"].cell(row=4, column=2, value="Bước không tồn tại")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with pytest.raises(FlowImportError) as exc:
        FlowImportService.import_workbook(buf)
    assert exc.value.errors[0]["sheet"] == "tacnghiepcauhinh"
    assert OperationStage.objects.count() == 0  # atomic rollback
