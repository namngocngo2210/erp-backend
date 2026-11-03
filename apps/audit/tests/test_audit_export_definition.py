"""Test `ExportDefinition` "audit_logs" của app audit — Task 16 của dự án
"xuất file chạy nền".

Mirror `AuditLogViewSet.export` gốc (action đã xoá ở Task 18): cùng 10 cột
`_EXPORT_COLUMNS`, cùng trần CẮT-LẶNG-LẼ (không raise) `_EXPORT_MAX_ROWS`, cùng
việc siết quyền xem theo user (`user_has_any_permission(user, "audit:view")` =
`require_section("audit")`, xem docstring `exports_definitions.py`).

`file_ext = "csv"` — khác 6 kind kia đều xlsx.
"""
from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.audit.transport.views.audit_views_impl import AuditLogViewSet
from apps.exports.application.registry import get_definition
from apps.users.tests.helpers import make_admin, make_user_with_sections

pytestmark = pytest.mark.django_db


class _RecordingWriter:
    def __init__(self):
        self.headers: list = []
        self.rows: list[list] = []

    def write_header(self, headers):
        self.headers = list(headers)

    def write_row(self, values):
        self.rows.append(list(values))


def _make_log(user=None, action_type="API_REQUEST") -> AuditLog:
    return AuditLog.objects.create(
        user=user,
        action_type=action_type,
        entity_type="OTHER",
        method="GET",
        source="api_request",
    )


def test_audit_export_is_csv():
    """Snippet gốc brief Step 4 — giữ nguyên."""
    definition = get_definition("audit_logs")
    assert definition.file_ext == "csv"
    assert definition.file_name({}).endswith(".csv")


def test_validate_scopes_user_id_and_filters_known_keys():
    definition = get_definition("audit_logs")
    admin = make_admin("audit-def-admin-validate")
    params = definition.validate(admin, {"action_type": "LOGIN", "unknown_key": "rác"})
    assert params["user_id"] == admin.id
    assert params["filters"] == {"action_type": "LOGIN"}


def test_write_scopes_to_own_logs_for_non_admin():
    """Mirror `test_export_scopes_to_own_logs_for_non_admin`: log CHÍNH staff và
    log CỦA admin khác — staff chỉ thấy log của mình dù chạy qua definition, nhờ
    `_scoped_queryset` tái dùng nguyên `AuditLogViewSet.get_queryset`."""
    staff = make_user_with_sections("audit-def-staff-scope", ["audit"])
    other = make_admin("audit-def-other-scope")
    _make_log(user=staff)
    _make_log(user=other)

    definition = get_definition("audit_logs")
    params = definition.validate(staff, {})
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)

    usernames = [row[1] for row in writer.rows]
    assert str(staff) in usernames
    assert str(other) not in usernames


def test_write_produces_10_export_columns():
    admin = make_admin("audit-def-admin-cols")
    _make_log(user=admin)

    definition = get_definition("audit_logs")
    params = definition.validate(admin, {})
    writer = _RecordingWriter()
    progressed: list[int] = []
    definition.write(params, writer, progressed.append)

    assert writer.headers == list(AuditLogViewSet._EXPORT_COLUMNS)
    assert len(writer.headers) == 10
    assert len(writer.rows) == 1
    assert len(writer.rows[0]) == 10
    assert progressed == sorted(progressed)
    assert progressed[-1] == len(writer.rows)


def test_write_applies_action_type_filter():
    admin = make_admin("audit-def-admin-filter")
    _make_log(user=admin, action_type="LOGIN")
    _make_log(user=admin, action_type="API_REQUEST")

    definition = get_definition("audit_logs")
    params = definition.validate(admin, {"action_type": "LOGIN"})
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)

    assert len(writer.rows) == 1
    assert writer.rows[0][2] == "LOGIN"


def test_estimate_caps_at_export_max_rows(monkeypatch):
    """Không raise khi vượt trần (khác warranty_claims/stock) — `estimate()` trả
    số dòng THỰC SỰ sẽ được ghi (bị cắt ở cap), không phải tổng số khớp filter,
    để progress bar không treo ở mốc không bao giờ tới."""
    import apps.audit.exports_definitions as exports_definitions

    admin = make_admin("audit-def-admin-estimate")
    for _ in range(3):
        _make_log(user=admin)

    monkeypatch.setattr(exports_definitions, "_EXPORT_MAX_ROWS", 2)
    definition = get_definition("audit_logs")
    params = definition.validate(admin, {})
    assert definition.estimate(params) == 2


def test_write_truncates_silently_without_warning_row(monkeypatch):
    """GIỮ NGUYÊN hành vi view đồng bộ: vượt trần chỉ `[:cap]`, KHÔNG cảnh báo gì
    cả — khác `warranty_claims`/`stock` (có dòng cảnh báo khi vượt trần)."""
    import apps.audit.exports_definitions as exports_definitions

    admin = make_admin("audit-def-admin-truncate")
    for _ in range(3):
        _make_log(user=admin)

    monkeypatch.setattr(exports_definitions, "_EXPORT_MAX_ROWS", 2)
    definition = get_definition("audit_logs")
    params = definition.validate(admin, {})
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)

    assert len(writer.rows) == 2
