"""Unit test thuần (zero-DB, chạy nhanh) của app orders.

Gộp từ test_vietnam_regions.py, test_kiotviet_job_error_message.py,
test_product_parser.py, test_lark_notifier.py. Mỗi section giữ nguyên docstring
gốc của file nguồn.
"""

from django.test import SimpleTestCase, override_settings

from apps.orders.infrastructure.lark_notifier import DEFAULT_MESSAGE_TEMPLATE, _render_message
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import (
    _build_error_message,
    _resolve_kv_failure_message,
)
from apps.orders.product_parser import derive_quantity, derive_total_amount
from apps.orders.vietnam_regions import (
    REGION_LABELS,
    REGION_PROVINCES,
    UNKNOWN_PROVINCE_LABEL,
    UNKNOWN_REGION,
    canonical_province,
    province_from_location,
    region_of,
    resolve_province,
)

# ---------------------------------------------------------------------------
# vietnam_regions — Map tỉnh → vùng: chuẩn hoá biến thể chính tả + phủ đủ 63 tỉnh.
# ---------------------------------------------------------------------------


class VietnamRegionsTests(SimpleTestCase):
    def test_covers_63_provinces_without_duplicates(self):
        provinces = [p for group in REGION_PROVINCES.values() for p in group]
        self.assertEqual(len(provinces), 63)
        self.assertEqual(len(set(provinces)), 63)
        self.assertEqual(set(REGION_PROVINCES), set(REGION_LABELS))

    def test_canonical_merges_diacritic_and_case_variants(self):
        # 3 biến thể Hoà Bình có thật trong dữ liệu prod → phải về cùng 1 tên.
        for raw in ("Hoà Bình", "Hòa Bình", "hòa Bình", "  HOA BINH  "):
            self.assertEqual(canonical_province(raw), "Hòa Bình")
        self.assertEqual(region_of("Hoà Bình"), region_of("Hòa Bình"))

    def test_region_assignment(self):
        self.assertEqual(region_of("Hà Nội"), "red_river_delta")
        self.assertEqual(region_of("Hồ Chí Minh"), "southeast")
        self.assertEqual(region_of("Cần Thơ"), "mekong_delta")
        self.assertEqual(region_of("Đắk Lắk"), "central_highlands")
        self.assertEqual(region_of("Huế"), "north_central")
        self.assertEqual(region_of("Đà Nẵng"), "south_central")
        # Đông Bắc gộp chung với Tây Bắc (chốt business 2026-07-20).
        self.assertEqual(region_of("Quảng Ninh"), "northern_mountains")
        self.assertEqual(region_of("Sơn La"), "northern_mountains")

    def test_aliases_and_unknown(self):
        self.assertEqual(canonical_province("TP HCM"), "Hồ Chí Minh")
        self.assertEqual(region_of("tphcm"), "southeast")
        self.assertEqual(canonical_province("Thừa Thiên Huế"), "Huế")
        self.assertEqual(region_of("Đắk Lắk"), region_of("dak lak"))
        # Tên lạ: giữ nguyên bản trim, vùng = unknown (không nuốt dữ liệu).
        self.assertEqual(canonical_province("  Tỉnh Lạ  "), "Tỉnh Lạ")
        self.assertEqual(region_of("Tỉnh Lạ"), UNKNOWN_REGION)
        self.assertEqual(region_of(""), UNKNOWN_REGION)


class ProvinceFromLocationTests(SimpleTestCase):
    """Suy tỉnh từ kiotviet_location khi `province` trống."""

    def test_extracts_province_prefix(self):
        self.assertEqual(province_from_location("Hà Nội - Quận Cầu Giấy"), "Hà Nội")
        self.assertEqual(province_from_location("Cần Thơ - Quận Ninh Kiều"), "Cần Thơ")
        self.assertEqual(province_from_location("Hưng Yên - Huyện Văn Lâm"), "Hưng Yên")

    def test_matches_longest_province_name_first(self):
        # Cắt theo dấu gạch ĐẦU TIÊN sẽ ra "Bà Rịa" (sai tỉnh) — phải khớp tên dài nhất.
        self.assertEqual(
            province_from_location("Bà Rịa - Vũng Tàu - Thành phố Phú Mỹ"),
            "Bà Rịa - Vũng Tàu",
        )

    def test_accepts_new_2_level_location_name(self):
        # Từ 09/2026 Khu vực Kiot tự điền theo 34 tỉnh mới: "Tỉnh X" / "Thành phố X".
        self.assertEqual(province_from_location("Tỉnh Đắk Lắk"), "Đắk Lắk")
        self.assertEqual(province_from_location("Thành phố Hà Nội"), "Hà Nội")
        self.assertEqual(province_from_location("Tỉnh Lâm Đồng"), "Lâm Đồng")

    def test_no_match_returns_blank(self):
        self.assertEqual(province_from_location(""), "")
        self.assertEqual(province_from_location("Không rõ - Xã ABC"), "")


class ResolveProvinceTests(SimpleTestCase):
    def test_prefers_province_field(self):
        self.assertEqual(resolve_province("Hoà Bình", "Hà Nội - Quận Cầu Giấy"), "Hòa Bình")

    def test_falls_back_to_location(self):
        self.assertEqual(resolve_province("", "Thái Nguyên - Thành phố Thái Nguyên"), "Thái Nguyên")
        self.assertEqual(resolve_province("   ", "Hải Phòng - Huyện An Lão"), "Hải Phòng")

    def test_no_address_at_all(self):
        # Đơn mới chưa nhập địa chỉ — vào nhóm riêng để tổng báo cáo không hụt.
        self.assertEqual(resolve_province("", ""), UNKNOWN_PROVINCE_LABEL)
        self.assertEqual(region_of(resolve_province("", "")), UNKNOWN_REGION)


# ---------------------------------------------------------------------------
# ingest_job_tasks — Unit tests cho message lỗi job KiotViet — yêu cầu: giữ 100%
# lỗi kiot trả về.
# ---------------------------------------------------------------------------


class TestBuildErrorMessage:
    def test_skips_cause_already_contained_in_parent(self):
        # Kiểu wrap phổ biến: Exception(f"[stage=x] {exc}") from exc — cause lặp
        # nguyên văn body response, không được xuất hiện 2 lần.
        body = '{"responseStatus":{"message":"Chi nhánh không hợp lệ"}}'
        inner = Exception(f"Failed to create invoice: 400 - {body}")
        outer = Exception(f"[stage=create_invoice] Failed to create invoice: 400 - {body}")
        outer.__cause__ = inner

        result = _build_error_message(outer)

        assert result.count(body) == 1
        assert "cause[1]" not in result

    def test_keeps_cause_with_new_information(self):
        inner = Exception("connection reset by peer")
        outer = Exception("[stage=push_order] push failed")
        outer.__cause__ = inner

        result = _build_error_message(outer)

        assert "push failed" in result
        assert "cause[1] Exception: connection reset by peer" in result

    def test_single_exception_without_cause(self):
        result = _build_error_message(ValueError("Thiếu mã kho"))
        assert result == "ValueError: Thiếu mã kho"


class TestResolveKvFailureMessage:
    def test_prefers_payload_message_verbatim(self):
        payload = {"message": "KShip: địa chỉ nhận không hợp lệ"}
        assert (
            _resolve_kv_failure_message(payload, "failed")
            == "KShip: địa chỉ nhận không hợp lệ"
        )

    def test_falls_back_to_status_map_when_message_empty(self):
        assert (
            _resolve_kv_failure_message({}, "rate_limited")
            == "KiotViet đang giới hạn tốc độ, vui lòng thử lại sau."
        )

    def test_generic_fallback_for_unknown_status(self):
        assert (
            _resolve_kv_failure_message({}, "failed")
            == "KiotViet trả lỗi nhưng không kèm mô tả."
        )


# ---------------------------------------------------------------------------
# product_parser — Bóc số tiền/số lượng từ text landing (product_parser).
#
# Case sai thực tế 2026-08-04 (đơn 0388581760): text "... MSC06 giá 1.599.000 +
# Voucher 100.000 ..." bị nhận thành 599.000 — regex của giá 599.000 khớp vào PHẦN
# ĐUÔI của "1.599.000" vì dấu chấm ngăn nghìn tạo word boundary. Số tiền chỉ được
# khớp khi đứng độc lập, không phải đuôi của một số lớn hơn.
# ---------------------------------------------------------------------------


def test_msc06_price_parsed_whole_not_suffix():
    text = (
        "1 Máy massage chân cao cấp MSC06 giá 1.599.000 "
        "+ Voucher 100.000 cho đơn tiếp theo"
    )
    assert derive_total_amount(text) == 1599000


def test_amount_never_matches_suffix_of_bigger_number():
    # 1.599.000 không được rơi về 599.000 dù 1.599.000 không có trong bảng.
    assert derive_total_amount("giá 21.599.000") != 599000
    # các biến thể ngăn cách: chấm / phẩy / khoảng trắng
    assert derive_total_amount("giá 1,599,000") == 1599000
    assert derive_total_amount("giá 1 599 000") == 1599000


def test_standalone_amounts_still_parse():
    assert derive_total_amount("bồn ngâm chân giá 599.000") == 599000
    assert derive_total_amount("mua 1 thảm 1.290.000 vnđ") == 1290000
    # combo đặc biệt 99.000 + 30.000 ship giữ nguyên
    assert derive_total_amount("gói thảo dược giá 99.000 + 30.000 phí ship") == 129000


def test_quantity_rules_unchanged():
    assert derive_quantity("Mua 1 Máy massage chân cao cấp MSC06") == 1
    assert derive_quantity("mua 2 thảm massage") == 2


class RenderOrderCreatedMessageTests(SimpleTestCase):
    """Message báo đơn mới qua Lark — kèm tên NV được gán (auto-chia lead / manual)."""

    def test_default_template_leads_with_staff_name(self):
        self.assertEqual(
            _render_message("Kênh A", "Nguyễn Văn A"),
            "[Nguyễn Văn A] Có đơn hàng mới ở kênh Kênh A, check nha",
        )

    def test_missing_staff_renders_chua_gan(self):
        # Đơn đổ về ngoài giờ làm việc: bộ chia lead không gán ai.
        self.assertTrue(_render_message("Kênh A", None).startswith("[chưa gán] "))
        self.assertTrue(_render_message("Kênh A", "   ").startswith("[chưa gán] "))

    def test_missing_channel_renders_dash(self):
        self.assertIn("kênh —", _render_message(None, "NV B"))

    @override_settings(LARK_ORDER_BOT_MESSAGE="Đơn mới kênh {channel_name}")
    def test_custom_template_without_staff_placeholder_gets_staff_prefixed(self):
        # Mẫu env CŨ (đặt trước 2026-08-05) không có ô {staff_name}. Trước đây tên
        # sale bị `format` bỏ im lặng → tin trên Lark vẫn không có NV phụ trách dù
        # code đã truyền tên xuống. Nay ghép bù vào đầu tin.
        self.assertEqual(
            _render_message("Kênh A", "NV B"),
            "[NV B] Đơn mới kênh Kênh A",
        )

    @override_settings(LARK_ORDER_BOT_MESSAGE="Đơn mới kênh {channel_name}")
    def test_prefixed_staff_falls_back_to_chua_gan(self):
        self.assertEqual(
            _render_message("Kênh A", None),
            "[chưa gán] Đơn mới kênh Kênh A",
        )

    @override_settings(LARK_ORDER_BOT_MESSAGE="Đơn kênh {channel_name} — NV {staff_name!r}")
    def test_staff_placeholder_with_format_spec_is_not_double_prefixed(self):
        # Ô có conversion/format spec vẫn là ô {staff_name} — không được ghép bù thêm.
        self.assertEqual(_render_message("Kênh A", "NV B"), "Đơn kênh Kênh A — NV 'NV B'")

    @override_settings(LARK_ORDER_BOT_MESSAGE="Đơn kênh {channel_name} gán cho {staff_name}")
    def test_custom_template_with_staff_placeholder(self):
        self.assertEqual(_render_message("Kênh A", "NV B"), "Đơn kênh Kênh A gán cho NV B")

    @override_settings(LARK_ORDER_BOT_MESSAGE="Hỏng {unknown_key}")
    def test_invalid_template_falls_back_to_default(self):
        self.assertEqual(
            _render_message("Kênh A", "NV B"),
            DEFAULT_MESSAGE_TEMPLATE.format(channel_name="Kênh A", staff_name="NV B"),
        )
