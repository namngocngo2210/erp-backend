"""Test `CallQualityExport` — definition đăng ký kind "call_quality" vào engine apps.exports.

Trọng tâm là ``TestCallQualityExportParity``: với CÙNG bộ lọc, tập cuộc gọi trong
sheet 1 phải KHỚP CHÍNH XÁC tập mà endpoint danh sách trả về. Cả hai đi qua
``quality_reports.filter_calls`` chính là để không sinh ra "đường lọc thứ hai" —
bài học từ trang Đơn hàng, nơi bộ lọc bị chép sang 5 chỗ và file Excel âm thầm
lệch khỏi màn hình.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.callcenter.models import CallQualityScore, CallRecord, TranscribeStatus
from apps.callcenter.qa_rubric import RUBRIC_VERSION
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.exports.application.registry import get_definition
from apps.orders.models import Order, OrderAssessment
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections

pytestmark = pytest.mark.django_db

CALLS_URL = "/api/callcenter/quality/calls"


class _RecordingWriter:
    """Ghi lại nội dung theo TỪNG SHEET.

    Khác writer của các export một sheet: định nghĩa này gọi ``set_sheet_title``
    rồi ``start_sheet`` hai lần, và thứ dễ hỏng nhất chính là dòng rơi nhầm sheet.
    """

    def __init__(self):
        self.sheets: list[dict] = []
        self._open("Sheet1")

    def _open(self, title: str) -> None:
        self.sheets.append({"title": title, "headers": [], "rows": []})

    def set_sheet_title(self, title):
        self.sheets[-1]["title"] = title

    def start_sheet(self, title):
        self._open(title)

    def write_header(self, headers):
        self.sheets[-1]["headers"] = list(headers)

    def write_row(self, values):
        self.sheets[-1]["rows"].append(list(values))


def _write(definition, params) -> _RecordingWriter:
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)
    return writer


def _sheet_dicts(sheet: dict) -> list[dict]:
    return [dict(zip(sheet["headers"], row)) for row in sheet["rows"]]


def _make_score(
    *,
    call_id: str,
    total: float,
    call_date: date,
    sales_status: str | None = None,
    staff_name: str = "",
    criteria: dict | None = None,
    red_flags: list | None = None,
) -> CallQualityScore:
    record = CallRecord.objects.create(
        call_id=call_id,
        transcribe_status=TranscribeStatus.DONE,
        transcript="Nhân viên: alo ạ\nKhách: vâng",
        recording_url="https://example.test/a.mp3",
        caller="0987000111",
        acd_time=300,
        start_time=timezone.make_aware(
            timezone.datetime(call_date.year, call_date.month, call_date.day, 10, 0)
        ),
    )
    order = None
    if sales_status is not None or staff_name:
        channel, _ = Channel.objects.get_or_create(
            code="cq-export", defaults={"name": "Kênh QA"}
        )
        customer = Customer.objects.create(full_name="Khách QA", phone_raw="0900000000")
        staff = None
        if staff_name:
            staff = make_user_with_sections(f"cq-{call_id}", ["landing-orders"])
            staff.display_name = staff_name
            staff.save(update_fields=["display_name"])
        order = Order.objects.create(
            channel=channel, customer=customer, staff=staff, name="Khách QA"
        )
        if sales_status is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status)
        record.order = order
        record.save(update_fields=["order"])

    return CallQualityScore.objects.create(
        call_record=record,
        rubric_version=RUBRIC_VERSION,
        call_date=call_date,
        order_id_snapshot=order.id if order else None,
        duration_seconds=300,
        total_score=total,
        block_scores={"A": 20, "B": 20, "C": 20, "D": 20},
        criteria=criteria
        or {
            "a1_greeting": {"score": 2, "na": False, "evidence": "Nhân viên: alo ạ"},
            "b1_discovery": {"score": 0, "na": False, "evidence": ""},
            "d2_info_complete": {"score": None, "na": True, "evidence": ""},
        },
        red_flags=red_flags or [],
        summary="nhận xét",
        wpm=120,
        talk_ratio=0.6,
    )


@pytest.fixture
def exporter():
    return make_user_with_permissions(
        "cq-exporter", ["call-quality:view", "call-quality:export"]
    )


@pytest.fixture
def today():
    return timezone.localdate()


# ───────────────────────────────── đăng ký ────────────────────────────────────


# ───────────────────────────────── tham số ────────────────────────────────────


def test_validate_chot_khoang_ngay_thanh_ngay_cu_the(exporter):
    """Job chạy nền có thể khởi động sau nửa đêm — để rỗng là file lệch một ngày."""
    definition = get_definition("call_quality")

    params = definition.validate(exporter, {})

    assert params["filters"]["start"] == (timezone.localdate() - timedelta(days=29)).isoformat()
    assert params["filters"]["end"] == timezone.localdate().isoformat()


def test_validate_giu_nguyen_ten_param_cua_endpoint_danh_sach(exporter):
    definition = get_definition("call_quality")

    params = definition.validate(
        exporter,
        {
            "filters": {
                "start": "2026-08-01",
                "end": "2026-08-10",
                "sales_status": "created_order",
                "staff": 7,
                "sku": "DLX-01",
                "criterion": "b1_discovery",
                "min_score": 40,
                "max_score": "90",
                "has_red_flag": True,
            }
        },
    )

    assert params["filters"] == {
        "start": "2026-08-01",
        "end": "2026-08-10",
        "sales_status": "created_order",
        "staff": "7",
        "sku": "DLX-01",
        "criterion": "b1_discovery",
        "min_score": 40,
        "max_score": 90,
        "has_red_flag": True,
    }


def test_validate_dao_lai_khoang_ngay_bi_nguoc(exporter):
    definition = get_definition("call_quality")

    params = definition.validate(
        exporter, {"filters": {"start": "2026-08-10", "end": "2026-08-01"}}
    )

    assert params["filters"]["start"] == "2026-08-01"
    assert params["filters"]["end"] == "2026-08-10"


@pytest.mark.parametrize(
    "bad_filters",
    [
        {"start": "10/08/2026"},
        {"end": "khong-phai-ngay"},
        {"min_score": "abc"},
        {"sku": 123},
    ],
)
def test_validate_tra_400_chu_khong_500(exporter, bad_filters):
    definition = get_definition("call_quality")

    with pytest.raises(ValidationError):
        definition.validate(exporter, {"filters": bad_filters})


def test_validate_tu_choi_filters_khong_phai_dict(exporter):
    definition = get_definition("call_quality")

    with pytest.raises(ValidationError):
        definition.validate(exporter, {"filters": "tat-ca"})


# ───────────────────────────────── quyền ──────────────────────────────────────


# ───────────────────────────────── trần dòng ──────────────────────────────────


def test_estimate_dem_dung_so_cuoc_khop(exporter, today):
    definition = get_definition("call_quality")
    for i in range(3):
        _make_score(call_id=f"cq-dem-{i}", total=80, call_date=today)

    assert definition.estimate(definition.validate(exporter, {})) == 3


def test_estimate_chan_khi_vuot_tran(exporter, today, monkeypatch):
    from apps.callcenter import exports_definitions

    definition = get_definition("call_quality")
    for i in range(3):
        _make_score(call_id=f"cq-tran-{i}", total=80, call_date=today)
    monkeypatch.setattr(exports_definitions, "QUALITY_EXPORT_MAX_ROWS", 2)

    with pytest.raises(ValidationError):
        definition.estimate(definition.validate(exporter, {}))


# ───────────────────────────────── nội dung file ──────────────────────────────


def test_file_co_dung_3_sheet_theo_thu_tu(exporter, today):
    definition = get_definition("call_quality")
    _make_score(call_id="cq-3sheet", total=80, call_date=today, sales_status="created_order")

    writer = _write(definition, definition.validate(exporter, {}))

    assert [sheet["title"] for sheet in writer.sheets] == [
        "Cuộc gọi",
        "Điểm tiêu chí",
        "Tổng hợp",
    ]


def test_sheet_cuoc_goi_khop_gia_tri_tren_man_hinh(exporter, today):
    definition = get_definition("call_quality")
    _make_score(
        call_id="cq-row",
        total=85,
        call_date=today,
        sales_status="need_more_consulting",
        staff_name="Sale A",
        red_flags=[{"code": "rf_medical_claim", "evidence": "chữa khỏi bệnh"}],
    )

    row = _sheet_dicts(_write(definition, definition.validate(exporter, {})).sheets[0])[0]

    assert row["Ngày gọi"] == today.isoformat()
    assert row["Sale phụ trách đơn"] == "Sale A"
    assert row["Số điện thoại"] == "0987000111"
    assert row["Trạng thái sale"] == "Tư vấn thêm"
    assert row["Điểm tổng"] == 85.0
    assert row["Đạt"] == "Đạt"
    assert row["Từ/phút"] == 120


@pytest.mark.django_db
def test_diem_tong_la_so_chu_khong_phai_chu(exporter, today):
    definition = get_definition("call_quality")
    _make_score(call_id="cq-number", total=72.5, call_date=today)

    row = _sheet_dicts(_write(definition, definition.validate(exporter, {})).sheets[0])[0]

    assert isinstance(row["Điểm tổng"], float)
    assert row["Đạt"] == "Chưa đạt"


def test_sheet_tieu_chi_bung_mot_dong_moi_tieu_chi_va_de_trong_o_na(exporter, today):
    """`na` là "không có cơ hội làm", 0 là "làm nhưng sai" — gộp lại là bóp méo số."""
    definition = get_definition("call_quality")
    _make_score(call_id="cq-crit", total=80, call_date=today)

    rows = _sheet_dicts(_write(definition, definition.validate(exporter, {})).sheets[1])
    by_code = {row["Mã tiêu chí"]: row for row in rows}

    assert len(rows) == 3
    assert by_code["a1_greeting"]["Điểm"] == 2
    assert by_code["a1_greeting"]["Trích dẫn"] == "Nhân viên: alo ạ"
    assert by_code["b1_discovery"]["Điểm"] == 0
    assert by_code["d2_info_complete"]["Điểm"] == ""
    assert by_code["d2_info_complete"]["Không áp dụng"] == "x"


def test_sheet_tong_hop_co_phan_ra_theo_trang_thai_sale(exporter, today):
    definition = get_definition("call_quality")
    _make_score(call_id="cq-sum-1", total=60, call_date=today, sales_status="created_order")
    _make_score(call_id="cq-sum-2", total=80, call_date=today, sales_status="created_order")
    _make_score(
        call_id="cq-sum-3", total=40, call_date=today, sales_status="need_more_consulting"
    )

    rows = _write(definition, definition.validate(exporter, {})).sheets[2]["rows"]
    flat = [row for row in rows if row]
    start = flat.index(["THEO TRẠNG THÁI SALE"])
    breakdown = {row[1]: (row[2], row[3]) for row in flat[start + 2 :]}

    assert breakdown["Đã lên đơn"] == (2, 70.0)
    assert breakdown["Tư vấn thêm"] == (1, 40.0)


def test_sheet_tong_hop_goi_ten_nhom_chua_khop_don(exporter, today):
    """Một nhóm lớn không tên là thứ làm người đọc mất niềm tin vào cả bảng."""
    definition = get_definition("call_quality")
    _make_score(call_id="cq-sum-noorder", total=50, call_date=today)

    rows = _write(definition, definition.validate(exporter, {})).sheets[2]["rows"]

    assert any(len(row) > 1 and row[1] == "(chưa khớp đơn)" for row in rows)


# ───────────────────────────── khớp với màn hình ──────────────────────────────


class TestCallQualityExportParity:
    """Cùng bộ lọc → sheet 1 và endpoint danh sách phải ra CÙNG tập cuộc gọi."""

    @pytest.fixture(autouse=True)
    def _data(self, today):
        self.today = today
        _make_score(
            call_id="p-chot", total=90, call_date=today, sales_status="created_order",
            staff_name="Sale Chốt",
        )
        _make_score(
            call_id="p-tuvan", total=50, call_date=today,
            sales_status="need_more_consulting", staff_name="Sale Tư Vấn",
        )
        _make_score(
            call_id="p-cu", total=70, call_date=today - timedelta(days=90),
            sales_status="created_order",
        )

    def _screen_ids(self, viewer, params) -> set:
        client = APIClient()
        client.force_authenticate(viewer)
        return {row["id"] for row in client.get(CALLS_URL, params).json()["items"]}

    def _file_dates(self, exporter, filters) -> list:
        definition = get_definition("call_quality")
        params = definition.validate(exporter, {"filters": filters})
        return _sheet_dicts(_write(definition, params).sheets[0])

    @pytest.mark.parametrize(
        "filters",
        [
            {},
            {"sales_status": "created_order"},
            {"sales_status": "need_more_consulting"},
            {"min_score": 60},
            {"criterion": "b1_discovery"},
        ],
    )
    def test_file_khop_endpoint_danh_sach(self, exporter, filters):
        screen = self._screen_ids(exporter, filters)
        rows = self._file_dates(exporter, filters)

        assert len(rows) == len(screen)

    def test_loc_trang_thai_sale_ap_dung_cho_file(self, exporter):
        rows = self._file_dates(exporter, {"sales_status": "created_order"})

        assert [row["Trạng thái sale"] for row in rows] == ["Đã lên đơn"]

    def test_file_khong_bi_phan_trang_cat(self, exporter):
        """Endpoint trả 50 dòng/trang; file luôn là TOÀN BỘ tập khớp bộ lọc."""
        for i in range(60):
            _make_score(call_id=f"p-nhieu-{i}", total=80, call_date=self.today)

        rows = self._file_dates(exporter, {})

        # 60 cuộc mới + 2 cuộc của fixture nằm trong kỳ; cuộc "p-cu" cách đây 90
        # ngày nằm ngoài khoảng mặc định nên không vào file.
        assert len(rows) == 62
