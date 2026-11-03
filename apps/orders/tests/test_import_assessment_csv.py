"""Command `import_assessment_csv` — ghi CRM (OrderAssessment) từ CSV mapping.

Command này ghi đè dữ liệu prod theo shipping_code/phone nên các nhánh nhạy cảm
phải khoá: ưu tiên shipping_code rồi mới phone, chọn đơn MỚI NHẤT khi nhiều đơn
cùng phone, mặc định KHÔNG ghi đè field đã có, `--overwrite` mới ghi đè,
`--dry-run` không để lại gì trong DB.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.orders.models import NeedGroup, NeedType, Order, OrderAssessment, OrderNeed

pytestmark = pytest.mark.django_db

COLUMNS = [
    "shipping_code",
    "phone",
    "sales_status_code",
    "age_text",
    "occupation_code",
    "pain_level_code",
    "pain_duration_code",
    "used_massage_before",
    "comparing_brand_text",
    "customer_classification_code",
    "internal_note",
    "delax_source_codes",
    "lifestyle_habit_codes",
    "relax_time_codes",
    "purchase_barrier_codes",
    "need_type_codes",
]


def _write_csv(tmp_path, rows: list[dict], *, name="mapping.csv", columns=COLUMNS, delimiter="|"):
    """CSV phân tách `|` (mặc định của command), thiếu khoá thì để trống."""
    lines = [delimiter.join(columns)]
    for row in rows:
        lines.append(delimiter.join(str(row.get(col, "")) for col in columns))
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _run(csv_path: str, *args: str) -> str:
    out = StringIO()
    call_command("import_assessment_csv", csv_path, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def _stat(output: str, key: str) -> int:
    for line in output.splitlines():
        if line.strip().startswith(f"{key} "):
            return int(line.split(":")[1].strip())
    raise AssertionError(f"Không thấy dòng thống kê {key!r} trong:\n{output}")


@pytest.fixture
def need_types():
    group = NeedGroup.objects.create(code="grp-test", name_vi="Nhóm test")
    return {
        code: NeedType.objects.create(code=code, group=group, name_vi=code)
        for code in ("dau_lung", "mat_ngu")
    }


# ── match theo shipping_code ─────────────────────────────────────────────────


def test_match_shipping_code_tao_assessment_day_du_va_doi_order_source(order_factory, need_types, tmp_path):
    order = order_factory(shipping_code="SPX001", phone="0901111111", order_source="messenger")
    csv_path = _write_csv(
        tmp_path,
        [
            {
                "shipping_code": "SPX001",
                "phone": "0000000000",  # phone sai — shipping_code phải thắng
                "sales_status_code": "closed",
                "age_text": "35",
                "occupation_code": "van_phong",
                "used_massage_before": "Yes",
                "internal_note": "ghi chú A",
                "delax_source_codes": "facebook, tiktok",
                "lifestyle_habit_codes": "it_van_dong",
                "need_type_codes": "dau_lung,mat_ngu,khong_ton_tai",
            }
        ],
    )

    output = _run(csv_path)

    order.refresh_from_db()
    assert order.order_source == "landing"
    assessment = OrderAssessment.objects.get(order=order)
    assert assessment.sales_status_code == "closed"
    assert assessment.age_text == "35_44"  # số tuổi → mã nhóm, không ghi giá trị thô
    assert assessment.occupation_code == "van_phong"
    assert assessment.used_massage_before is True
    assert assessment.internal_note == "ghi chú A"
    assert assessment.delax_source_codes == ["facebook", "tiktok"]
    assert assessment.lifestyle_habit_codes == ["it_van_dong"]
    assert sorted(
        OrderNeed.objects.filter(order_assessment=assessment).values_list("need_type__code", flat=True)
    ) == ["dau_lung", "mat_ngu"]

    assert _stat(output, "total") == 1
    assert _stat(output, "matched_shipping_code") == 1
    assert _stat(output, "matched_phone") == 0
    assert _stat(output, "unmatched") == 0
    assert _stat(output, "order_source_updated") == 1
    assert _stat(output, "assessment_created") == 1
    assert _stat(output, "assessment_updated") == 1
    assert _stat(output, "need_links_added") == 2
    # Mã need không có trong DB được liệt kê, không làm vỡ import.
    assert "khong_ton_tai: 1" in output


def test_shipping_code_khop_nhieu_don_thi_ap_cho_tat_ca(order_factory, tmp_path):
    first = order_factory(shipping_code="SPX002", phone="0901111111")
    second = order_factory(shipping_code="SPX002", phone="0902222222")
    other = order_factory(shipping_code="SPX999", phone="0903333333")
    csv_path = _write_csv(tmp_path, [{"shipping_code": "SPX002", "sales_status_code": "closed"}])

    output = _run(csv_path)

    assert OrderAssessment.objects.filter(order__in=[first, second], sales_status_code="closed").count() == 2
    assert not OrderAssessment.objects.filter(order=other).exists()
    assert _stat(output, "matched_multiple_siblings") == 1
    assert _stat(output, "assessment_created") == 2


# ── fallback phone ───────────────────────────────────────────────────────────


def test_khong_co_shipping_code_thi_match_phone_va_chon_don_moi_nhat(order_factory, tmp_path):
    older = order_factory(phone="0905555555")
    newer = order_factory(phone="0905555555")
    Order.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=3))
    Order.objects.filter(pk=newer.pk).update(created_at=timezone.now() - timedelta(days=1))
    csv_path = _write_csv(tmp_path, [{"phone": "0905555555", "sales_status_code": "scheduled"}])

    output = _run(csv_path)

    assert OrderAssessment.objects.get(order=newer).sales_status_code == "scheduled"
    assert not OrderAssessment.objects.filter(order=older).exists()
    assert _stat(output, "matched_phone") == 1
    assert _stat(output, "matched_shipping_code") == 0


def test_phone_so_sanh_9_so_cuoi_nen_84_va_0_deu_khop(order_factory, tmp_path):
    order = order_factory(phone="0905555555")
    csv_path = _write_csv(tmp_path, [{"phone": "+84 905 555 555", "sales_status_code": "closed"}])

    _run(csv_path)

    assert OrderAssessment.objects.get(order=order).sales_status_code == "closed"


def test_shipping_code_khong_khop_thi_roi_xuong_phone(order_factory, tmp_path):
    order = order_factory(shipping_code="THAT", phone="0906666666")
    csv_path = _write_csv(
        tmp_path, [{"shipping_code": "SAI-MA", "phone": "0906666666", "sales_status_code": "closed"}]
    )

    output = _run(csv_path)

    assert OrderAssessment.objects.get(order=order).sales_status_code == "closed"
    # Thống kê ghi theo cột có dữ liệu trong CSV (shipping_code), dù thực tế khớp qua phone.
    assert _stat(output, "matched_shipping_code") == 1


# ── overwrite ────────────────────────────────────────────────────────────────


def test_mac_dinh_khong_ghi_de_field_da_co_chi_dien_field_rong(order_factory, tmp_path):
    order = order_factory(shipping_code="SPX003", order_source="landing")
    OrderAssessment.objects.create(
        order=order,
        sales_status_code="closed",
        internal_note="",
        delax_source_codes=["facebook"],
        used_massage_before=False,
    )
    csv_path = _write_csv(
        tmp_path,
        [
            {
                "shipping_code": "SPX003",
                "sales_status_code": "scheduled",
                "internal_note": "note mới",
                "delax_source_codes": "tiktok",
                "used_massage_before": "yes",
            }
        ],
    )

    output = _run(csv_path)

    assessment = OrderAssessment.objects.get(order=order)
    assert assessment.sales_status_code == "closed"  # đã có → giữ
    assert assessment.internal_note == "note mới"  # rỗng → điền
    assert assessment.delax_source_codes == ["facebook"]  # mảng đã có → giữ nguyên
    # False ("chưa dùng massage") là dữ liệu thật, không phải "rỗng" — thiếu
    # --overwrite thì CSV "yes" KHÔNG được đè (bug audit 2026-09-23).
    assert assessment.used_massage_before is False
    assert _stat(output, "assessment_existing") == 1
    assert _stat(output, "assessment_created") == 0
    assert _stat(output, "order_source_updated") == 0


def test_overwrite_ghi_de_scalar_va_gop_mang(order_factory, tmp_path):
    order = order_factory(shipping_code="SPX004")
    OrderAssessment.objects.create(
        order=order,
        sales_status_code="closed",
        delax_source_codes=["facebook"],
        used_massage_before=False,
    )
    csv_path = _write_csv(
        tmp_path,
        [
            {
                "shipping_code": "SPX004",
                "sales_status_code": "scheduled",
                "delax_source_codes": "tiktok,facebook",
                "used_massage_before": "yes",
            }
        ],
    )

    output = _run(csv_path, "--overwrite")

    assessment = OrderAssessment.objects.get(order=order)
    assert assessment.sales_status_code == "scheduled"
    assert assessment.used_massage_before is True
    # Mảng: giữ thứ tự cũ, nối thêm mã mới chưa có, không nhân đôi.
    assert assessment.delax_source_codes == ["facebook", "tiktok"]
    assert _stat(output, "assessment_updated") == 1


@pytest.mark.parametrize(
    "current, raw, overwrite, expected",
    [
        (False, "yes", False, False),  # đã có False → giữ
        (False, "yes", True, True),  # --overwrite → đè
        (None, "yes", False, True),  # None mới là rỗng → điền
        (False, "", True, False),  # CSV rỗng → không đè kể cả --overwrite
        (True, "maybe", True, True),  # giá trị không hợp lệ → bỏ qua
    ],
)
def test_used_massage_before_false_la_gia_tri_that_khong_phai_rong(
    order_factory, tmp_path, current, raw, overwrite, expected
):
    order = order_factory(shipping_code="SPX010", order_source="landing")
    OrderAssessment.objects.create(order=order, used_massage_before=current)
    csv_path = _write_csv(tmp_path, [{"shipping_code": "SPX010", "used_massage_before": raw}])

    _run(csv_path, *(["--overwrite"] if overwrite else []))

    assert OrderAssessment.objects.get(order=order).used_massage_before is expected


def test_gia_tri_giong_het_khong_tinh_la_cap_nhat(order_factory, tmp_path):
    order = order_factory(shipping_code="SPX005", order_source="landing")
    OrderAssessment.objects.create(order=order, sales_status_code="closed")
    csv_path = _write_csv(tmp_path, [{"shipping_code": "SPX005", "sales_status_code": "closed"}])

    output = _run(csv_path, "--overwrite")

    assert _stat(output, "assessment_updated") == 0
    assert _stat(output, "assessment_existing") == 1


@pytest.mark.parametrize(
    "raw, expected",
    [("yes", True), ("No", False), ("maybe", None), ("", None)],
)
def test_used_massage_before_chi_nhan_yes_no(order_factory, tmp_path, raw, expected):
    order = order_factory(shipping_code="SPX006")
    csv_path = _write_csv(
        tmp_path, [{"shipping_code": "SPX006", "used_massage_before": raw, "sales_status_code": "closed"}]
    )

    _run(csv_path)

    assert OrderAssessment.objects.get(order=order).used_massage_before is expected


# ── unmatched / dry-run / lỗi ────────────────────────────────────────────────


def test_dong_khong_khop_don_duoc_dem_va_ghi_ra_file_unmatched(order_factory, tmp_path):
    order_factory(shipping_code="CO-THAT", phone="0907777777")
    csv_path = _write_csv(
        tmp_path,
        [
            {"shipping_code": "KHONG-CO", "phone": "0900000001", "sales_status_code": "closed"},
            {"shipping_code": "CO-THAT", "sales_status_code": "closed"},
        ],
    )
    unmatched_out = tmp_path / "unmatched.csv"

    output = _run(csv_path, "--unmatched-out", str(unmatched_out))

    assert _stat(output, "total") == 2
    assert _stat(output, "unmatched") == 1
    assert "shipping='KHONG-CO' phone='0900000001'" in output
    assert f"Đã ghi 1 dòng unmatched vào: {unmatched_out}" in output
    lines = unmatched_out.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("|") == COLUMNS
    assert lines[1].startswith("KHONG-CO|0900000001|closed|")
    assert OrderAssessment.objects.count() == 1


def test_csv_thieu_cot_khoa_thi_moi_dong_unmatched_khong_vo(order_factory, tmp_path):
    order_factory(shipping_code="SPX007")
    csv_path = _write_csv(
        tmp_path,
        [{"sales_status_code": "closed"}],
        columns=["sales_status_code", "internal_note"],
    )

    output = _run(csv_path)

    assert _stat(output, "total") == 1
    assert _stat(output, "unmatched") == 1
    assert "shipping='' phone=''" in output
    assert OrderAssessment.objects.count() == 0


def test_file_khong_ton_tai_raise_command_error(tmp_path):
    with pytest.raises(CommandError, match="Không mở được file"):
        _run(str(tmp_path / "khong-co.csv"))


def test_dry_run_khong_luu_gi(order_factory, tmp_path):
    order = order_factory(shipping_code="SPX008", order_source="messenger")
    csv_path = _write_csv(tmp_path, [{"shipping_code": "SPX008", "sales_status_code": "closed"}])

    output = _run(csv_path, "--dry-run")

    order.refresh_from_db()
    assert order.order_source == "messenger"
    assert not OrderAssessment.objects.filter(order=order).exists()
    assert "DRY-RUN: đã rollback mọi thay đổi." in output
    assert "Không có thay đổi nào được lưu." in output
    # Báo cáo vẫn phản ánh những gì ĐÃ ĐỊNH làm.
    assert _stat(output, "matched_shipping_code") == 1
    assert _stat(output, "assessment_created") == 1


def test_delimiter_tuy_chon(order_factory, tmp_path):
    order = order_factory(shipping_code="SPX009")
    csv_path = _write_csv(
        tmp_path, [{"shipping_code": "SPX009", "sales_status_code": "closed"}], delimiter=","
    )

    _run(csv_path, "--delimiter", ",")

    assert OrderAssessment.objects.get(order=order).sales_status_code == "closed"


# ── --total-csv: tạo đơn cho dòng unmatched ──────────────────────────────────

TOTAL_COLUMNS = ["channel_id", "name", "phone", "raw_address", "product_raw", "staff", "shipping_code"]


def test_total_csv_tao_order_moi_cho_dong_unmatched(tmp_path):
    channel = Channel.objects.create(code="lp-import", name="Landing Import")
    mapping = _write_csv(
        tmp_path,
        [{"shipping_code": "NEW001", "phone": "0908888888", "sales_status_code": "closed"}],
    )
    total = _write_csv(
        tmp_path,
        [
            {
                "channel_id": channel.id,
                "name": "Khách mới",
                "phone": "0908888888",
                "raw_address": "12 Lê Lợi",
                "product_raw": "DC12",
                "staff": "khong-co-user",
                "shipping_code": "NEW001",
            }
        ],
        name="total.csv",
        columns=TOTAL_COLUMNS,
    )

    output = _run(mapping, "--total-csv", total)

    order = Order.objects.get(shipping_code="NEW001")
    assert (order.channel_id, order.name, order.phone) == (channel.id, "Khách mới", "0908888888")
    assert (order.raw_address, order.product_raw, order.order_source) == ("12 Lê Lợi", "DC12", "landing")
    assert order.staff is None
    assert order.customer.phone_normalized == "0908888888"
    assert OrderAssessment.objects.get(order=order).sales_status_code == "closed"
    assert "Loaded total CSV: 1 by shipping_code, 1 by phone (last9)" in output
    assert _stat(output, "created_from_total") == 1
    assert _stat(output, "unmatched") == 0


def test_total_csv_thieu_channel_thi_khong_tao_va_van_unmatched(tmp_path):
    mapping = _write_csv(tmp_path, [{"shipping_code": "NEW002", "sales_status_code": "closed"}])
    total = _write_csv(
        tmp_path,
        [{"channel_id": "", "name": "Khách", "phone": "0908888889", "shipping_code": "NEW002"}],
        name="total.csv",
        columns=TOTAL_COLUMNS,
    )

    output = _run(mapping, "--total-csv", total)

    assert not Order.objects.filter(shipping_code="NEW002").exists()
    assert _stat(output, "created_skip_no_channel") == 1
    assert _stat(output, "unmatched") == 1
