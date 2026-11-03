from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.conversion_feed.domain import lead, params
from apps.conversion_feed.domain.postal_codes import PROVINCE_POSTAL_CODES
from apps.orders.vietnam_regions import REGION_PROVINCES

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("full", "expected"),
    [
        ("Nguyễn Văn An", ("Văn An", "Nguyễn")),
        ("  Trần   Thị  Lan ", ("Thị Lan", "Trần")),
        ("Lan", ("Lan", "")),
        ("", ("", "")),
    ],
)
def test_split_name_vietnamese_order(full, expected):
    assert lead.split_name(full) == expected


@pytest.mark.parametrize(
    ("order_source", "expected"),
    [(None, "googleads"), ("", "googleads"), ("landing", "googleads"), ("Hotline", "hotline"),
     ("zalo", "zalo"), ("messenger", "facebook"), ("kol", "kol")],
)
def test_map_source(order_source, expected):
    assert lead.map_source(order_source) == expected


def test_postal_code_covers_every_canonical_province():
    """Tên tỉnh chuẩn của vietnam_regions là khoá tra — thiếu tỉnh nào là zip rỗng im lặng."""
    canonical = {p for provinces in REGION_PROVINCES.values() for p in provinces}
    assert canonical == set(PROVINCE_POSTAL_CODES)
    assert lead.postal_code("Hà Nội") == "10000"
    assert lead.postal_code("Hồ Chí Minh") == "70000"
    assert lead.postal_code("Không rõ") == ""


def test_join_skus_falls_back_to_order_sku():
    assert lead.join_skus("A;B", "X") == "A;B"
    assert lead.join_skus(None, " X ") == "X"
    assert lead.to_vnd(Decimal("12490000.00")) == 12_490_000


def test_parse_range_defaults_to_last_7_days():
    start, end = params.parse_range({}, now=NOW, tz=TZ)
    assert end == NOW
    assert (end - start).days == 7


def test_parse_range_date_to_extends_to_end_of_day():
    start, end = params.parse_range({"from": "2026-09-01", "to": "2026-09-02"}, now=NOW, tz=TZ)
    assert start == datetime(2026, 9, 1, tzinfo=TZ)
    assert end == datetime.combine(date(2026, 9, 2), time.max, tzinfo=TZ)


def test_parse_range_accepts_iso_datetime_with_offset():
    start, _ = params.parse_range({"from": "2026-09-01T08:00:00+00:00", "to": "2026-09-02"}, now=NOW, tz=TZ)
    assert start == datetime(2026, 9, 1, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    "query",
    [{"from": "01/09/2026"}, {"from": "2026-09-05", "to": "2026-09-01"}, {"from": "2026-01-01", "to": "2026-09-01"}],
)
def test_parse_range_rejects_bad_input(query):
    with pytest.raises(params.ParamError):
        params.parse_range(query, now=NOW, tz=TZ)


def test_parse_limit_and_cursor():
    assert params.parse_limit({}) == params.DEFAULT_LIMIT
    assert params.parse_limit({"limit": "5000"}) == params.MAX_LIMIT
    assert params.parse_cursor({"cursor": "42"}) == 42
    assert params.parse_cursor({}) is None
    for bad in ({"limit": "0"}, {"limit": "abc"}):
        with pytest.raises(params.ParamError):
            params.parse_limit(bad)
    with pytest.raises(params.ParamError):
        params.parse_cursor({"cursor": "x"})
