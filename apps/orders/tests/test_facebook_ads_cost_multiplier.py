"""Unit test trực tiếp `facebook_ads_cost_multiplier()` — bảng mốc hệ số FB.

Trước đây bảng mốc (08/2026 ×1.08, từ 09/2026 về ×1.1, kỳ bắc qua mốc giữ hệ số
cũ) được kiểm lại qua HTTP ở 5 báo cáo mà không có test cho hàm gốc. Giờ: mốc và
biên ngày khoá ở đây; mỗi báo cáo chỉ giữ 1 test "có áp hệ số".
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.orders.reporting.shared import (
    ADS_DAILY_COST_MULTIPLIER,
    FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE,
    GOOGLE_ADS_DAILY_COST_MULTIPLIER,
    facebook_ads_cost_multiplier,
)


@pytest.mark.parametrize(
    ("date_from", "expected"),
    [
        pytest.param(None, Decimal("1.1"), id="khong-loc-ngay-giu-he-so-goc"),
        pytest.param(date(2026, 7, 31), Decimal("1.1"), id="truoc-moc-08"),
        pytest.param(date(2026, 8, 1), Decimal("1.08"), id="dung-moc-01-08"),
        pytest.param(date(2026, 8, 31), Decimal("1.08"), id="cuoi-thang-08"),
        pytest.param(date(2026, 9, 1), Decimal("1.1"), id="dung-moc-01-09-ve-lai-1.1"),
        pytest.param(date(2027, 1, 1), Decimal("1.1"), id="sau-moc-cuoi"),
    ],
)
def test_multiplier_by_period_start(date_from, expected):
    """Hệ số chọn theo ngày BẮT ĐẦU kỳ: kỳ bắc qua mốc (vd 31/07→31/08) vẫn ăn hệ
    số của ngày bắt đầu — đây chính là ca `date(2026, 7, 31)`."""
    assert facebook_ads_cost_multiplier(date_from) == expected


def test_schedule_is_sorted_newest_first():
    """Hàm duyệt bảng tuần tự và trả mốc ĐẦU TIÊN khớp → bảng phải xếp mốc mới
    trước, nếu không mốc 01/08 sẽ nuốt luôn các kỳ từ 09/2026."""
    effective_dates = [effective for effective, _ in FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE]
    assert effective_dates == sorted(effective_dates, reverse=True)
    assert FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE[0] == (date(2026, 9, 1), Decimal("1.1"))
    assert FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE[-1] == (date(2026, 8, 1), Decimal("1.08"))


def test_google_multiplier_is_a_separate_constant():
    """Google (VAT 8%) không đi qua bảng mốc FB: đổi một bên không kéo bên kia."""
    assert Decimal("1.08") == GOOGLE_ADS_DAILY_COST_MULTIPLIER
    assert Decimal("1.1") == ADS_DAILY_COST_MULTIPLIER
    assert facebook_ads_cost_multiplier(date(2026, 8, 15)) == GOOGLE_ADS_DAILY_COST_MULTIPLIER
    assert facebook_ads_cost_multiplier(date(2026, 9, 15)) != GOOGLE_ADS_DAILY_COST_MULTIPLIER
