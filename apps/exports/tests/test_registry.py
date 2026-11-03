import pytest

from apps.exports.application.registry import (
    ExportDefinition,
    UnknownExportKind,
    get_definition,
    list_kinds,
    register_export,
)


class _Dummy(ExportDefinition):
    kind = "dummy_test_kind"

    def validate(self, user, raw_params):
        return {"ok": True}

    def check_permission(self, user, params):
        return None

    def estimate(self, params):
        return 1

    def file_name(self, params):
        return "dummy.xlsx"

    def write(self, params, writer, progress):
        writer.write_header(["A"])
        writer.write_row(["x"])


def test_register_and_get():
    register_export(_Dummy())
    assert get_definition("dummy_test_kind").kind == "dummy_test_kind"
    assert "dummy_test_kind" in list_kinds()


def test_get_unknown_kind_raises():
    with pytest.raises(UnknownExportKind):
        get_definition("khong-ton-tai")


def test_register_duplicate_kind_raises():
    register_export(_Dummy())
    with pytest.raises(ValueError):
        register_export(_Dummy())
