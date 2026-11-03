import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.sysconfig.registry import GROUPS, Setting, coerce, validate_registry


def _s(**kw) -> Setting:
    base = {"key": "X_KEY", "type": "str", "default": "", "group": GROUPS[0][0], "label": "x"}
    base.update(kw)
    return Setting(**base)


def test_validate_rejects_duplicate_key():
    with pytest.raises(ImproperlyConfigured, match="trùng"):
        validate_registry([_s(), _s()])


def test_validate_rejects_unknown_group():
    with pytest.raises(ImproperlyConfigured, match="group"):
        validate_registry([_s(group="nope")])


def test_validate_rejects_default_wrong_type():
    with pytest.raises(ImproperlyConfigured, match="default"):
        validate_registry([_s(type="int", default="80")])


def test_validate_rejects_non_str_secret():
    with pytest.raises(ImproperlyConfigured, match="secret"):
        validate_registry([_s(type="bool", default=False, secret=True)])


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("YES", True), ("0", False), ("false", False), ("", False)],
)
def test_coerce_bool(raw, expected):
    assert coerce(_s(type="bool", default=False), raw) is expected


def test_coerce_bool_rejects_garbage():
    with pytest.raises(ValueError):
        coerce(_s(type="bool", default=False), "maybe")


def test_coerce_int_and_float():
    assert coerce(_s(type="int", default=0), " 80 ") == 80
    assert coerce(_s(type="float", default=0.0), "5") == 5.0
    with pytest.raises(ValueError):
        coerce(_s(type="int", default=0), "8.5")


def test_coerce_url_requires_scheme_or_empty():
    assert coerce(_s(type="url"), " https://a.b/c ") == "https://a.b/c"
    assert coerce(_s(type="url"), "") == ""
    with pytest.raises(ValueError):
        coerce(_s(type="url"), "a.b/c")


def test_coerce_str_strips():
    assert coerce(_s(), "  abc ") == "abc"


def test_coerce_datetime_validates_and_keeps_text():
    spec = _s(type="datetime", default="")
    assert coerce(spec, " 2026-10-01 08:00 ") == "2026-10-01 08:00"
    assert coerce(spec, "") == ""
    with pytest.raises(ValueError, match="thời điểm"):
        coerce(spec, "mai 8h")
    with pytest.raises(ValueError, match="thời điểm"):
        coerce(spec, "2026-13-40")


def test_parse_datetime_value_naive_is_local_time():
    from apps.sysconfig.registry import parse_datetime_value

    assert parse_datetime_value("2026-10-01 08:00").isoformat() == "2026-10-01T08:00:00+07:00"
    assert parse_datetime_value("2026-10-01").isoformat() == "2026-10-01T00:00:00+07:00"
    assert parse_datetime_value("2026-10-01T01:00:00+00:00").isoformat() == "2026-10-01T01:00:00+00:00"
    assert parse_datetime_value("") is None
    assert parse_datetime_value("x") is None
