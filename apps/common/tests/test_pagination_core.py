import pytest

from apps.common.pagination_core import (
    InvalidLimit,
    clamp_int_param,
    lenient_limit,
    lenient_offset,
    strict_limit,
)


def test_clamp_int_param():
    assert clamp_int_param("abc", default=20, minimum=1, maximum=100) == 20
    assert clamp_int_param(None, default=20, minimum=1, maximum=100) == 20
    assert clamp_int_param("0", default=20, minimum=1, maximum=100) == 1  # kẹp, không về default
    assert clamp_int_param("500", default=20, minimum=1, maximum=100) == 100
    assert clamp_int_param("-5", default=0, minimum=0) == 0


def test_lenient_limit_and_offset():
    assert lenient_limit(None, default=20, max_limit=100) == 20
    assert lenient_limit("abc", default=20, max_limit=100) == 20
    assert lenient_limit("0", default=20, max_limit=100) == 20  # về default, khác clamp
    assert lenient_limit("-3", default=20, max_limit=100) == 20
    assert lenient_limit("500", default=20, max_limit=100) == 100
    assert lenient_limit("50", default=20, max_limit=100) == 50
    assert lenient_offset(None) == 0
    assert lenient_offset("abc") == 0
    assert lenient_offset("-5") == 0
    assert lenient_offset("40") == 40


def test_strict_limit():
    assert strict_limit("", default=200, max_limit=1000) == 200
    assert strict_limit(None, default=200, max_limit=100) == 100  # trần nhỏ hơn mặc định
    assert strict_limit("50", default=200, max_limit=1000) == 50
    assert strict_limit("5000", default=200, max_limit=1000) == 1000
    for raw in ("0", "-1", "abc", "1.5"):
        with pytest.raises(InvalidLimit):
            strict_limit(raw, default=200, max_limit=1000)
