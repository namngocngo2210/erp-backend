from datetime import date

import pytest

from apps.common.dates import parse_ddmmyyyy, parse_iso_date


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_empty_is_none_even_when_strict(raw):
    assert parse_iso_date(raw) is None
    assert parse_iso_date(raw, strict=True) is None
    assert parse_ddmmyyyy(raw) is None


def test_iso_parses_and_strips():
    assert parse_iso_date("2026-09-23") == date(2026, 9, 23)
    assert parse_iso_date(" 2026-09-23 ") == date(2026, 9, 23)


@pytest.mark.parametrize("raw", ["10/08/2026", "khong-phai-ngay", "2026-01-01T00:00", 123, ["2026-01-01"]])
def test_invalid_is_none_when_lenient_and_raises_when_strict(raw):
    assert parse_iso_date(raw) is None
    with pytest.raises(ValueError):
        parse_iso_date(raw, strict=True)


def test_ddmmyyyy():
    assert parse_ddmmyyyy("23/09/2026") == date(2026, 9, 23)
    assert parse_ddmmyyyy("2026-09-23") is None
    assert parse_ddmmyyyy("14:30") is None
    with pytest.raises(ValueError):
        parse_ddmmyyyy("2026-09-23", strict=True)
