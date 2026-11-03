"""Hợp đồng chung của MỌI `ExportDefinition` đã đăng ký vào engine.

Trước đây 7 app chép cùng 4 test (`registered` / `check_permission` denies+allows /
`file_name` / `estimate is None`) cho từng definition — 25 định nghĩa gần giống
nhau. Gom về một chỗ, duyệt theo bảng `CASES`:

- `test_every_registered_kind_has_a_case` bắt buộc app mới thêm export phải khai
  vào bảng này (đúng quyền thiếu / đủ, thông báo, tên file) — không có vùng vô hình.
- Thông báo lỗi được assert NGUYÊN VĂN: `pytest.raises(PermissionDenied)` không
  bắt được definition bịa message riêng (bài học warranty round 1).
- Chỉ đụng registry + helper tạo user (tầng dưới `exports` theo .importlinter);
  KHÔNG import app nghiệp vụ — test cần dữ liệu thật (vd tên file theo lô mã BH)
  vẫn nằm ở app đó.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from rest_framework.exceptions import PermissionDenied

from apps.exports.application.registry import get_definition, list_kinds
from apps.users.tests.helpers import make_user_with_permissions

pytestmark = pytest.mark.django_db

GENERIC_DENIED = "Bạn không có quyền thực hiện hành động này."
PAGE_DENIED = "Bạn không có quyền xuất Excel ở trang này."


def _no_params(user) -> dict:
    return {}


def _user_scoped(user) -> dict:
    return {"user_id": user.id, "filters": {}}


def _orders_params(section: str) -> Callable[[object], dict]:
    return lambda user: {"section": section, "columns": [], "ids": [], "filters": {}}


@dataclass(frozen=True)
class Case:
    kind: str
    deny: tuple[str, ...]
    allow: tuple[str, ...]
    denied_message: str
    file_ext: str = "xlsx"
    # params cho check_permission — nhận user vì vài definition cần user_id trong params
    params: Callable[[object], dict] = _no_params
    # (params → tên file): str so bằng, re.Pattern so fullmatch (tên có timestamp)
    file_names: tuple[tuple[dict, str | re.Pattern], ...] = field(default_factory=tuple)
    # params mà estimate() phải trả None (luồng không đếm rẻ được)
    estimate_none: tuple[dict, ...] = field(default_factory=tuple)


CASES: tuple[Case, ...] = (
    Case(
        kind="audit_logs",
        deny=("orders:view",),
        allow=("audit:view",),
        denied_message=GENERIC_DENIED,
        file_ext="csv",
        file_names=(({}, "audit-logs.csv"),),
    ),
    Case(
        kind="call_quality",
        deny=("call-quality:view",),
        allow=("call-quality:export",),
        denied_message=PAGE_DENIED,
        params=_user_scoped,
        file_names=(({}, re.compile(r"chat-luong-cuoc-goi-\d{8}-\d{6}\.xlsx")),),
    ),
    Case(
        kind="stock",
        deny=("stock:view",),
        allow=("stock:export",),
        denied_message=GENERIC_DENIED,
    ),
    Case(
        kind="dispatch_history",
        deny=("stock:view",),
        allow=("stock:export",),
        denied_message=GENERIC_DENIED,
        file_names=(
            ({"group": ""}, "lich-su-xuat.xlsx"),
            ({"group": "sku"}, "lich-su-xuat-sku.xlsx"),
        ),
        estimate_none=({"group": ""}, {"group": "sku"}),
    ),
    Case(
        kind="return_history",
        deny=("stock:view",),
        allow=("stock:export",),
        denied_message=GENERIC_DENIED,
        file_names=(
            ({"group": ""}, "lich-su-nhap-hoan.xlsx"),
            ({"group": "sku"}, "lich-su-nhap-hoan-sku.xlsx"),
        ),
        estimate_none=({"group": ""}, {"group": "sku"}),
    ),
    Case(
        kind="orders",
        deny=("orders:view",),
        allow=("orders:export",),
        denied_message=PAGE_DENIED,
        params=_orders_params("orders"),
        file_names=(({}, re.compile(r"don-hang-\d{8}-\d{6}\.xlsx")),),
    ),
    Case(
        kind="contacts",
        deny=("contacts:view",),
        allow=("contacts:export",),
        denied_message=PAGE_DENIED,
        params=_user_scoped,
        file_names=(({}, re.compile(r"danh-sach-contact-\d{8}-\d{6}\.xlsx")),),
    ),
    Case(
        kind="warranty_code_batch",
        deny=("warranty-codes:view",),
        allow=("warranty-codes:view", "warranty-codes:export"),
        denied_message=GENERIC_DENIED,
        # file_name cần lô mã thật → apps/warranty_codes/tests/test_batch_export_definition.py
    ),
    Case(
        kind="warranty_claims",
        deny=("warranty:view", "warranty:create", "warranty:edit", "warranty:delete"),
        allow=("warranty:view", "warranty:export"),
        denied_message=GENERIC_DENIED,
        file_names=(({}, "phieu-bao-hanh.xlsx"),),
    ),
)
_BY_KIND = {case.kind: case for case in CASES}
_KINDS = [case.kind for case in CASES]


def test_every_registered_kind_has_a_case():
    """App thêm export mới thì phải khai vào CASES — không có definition ngoài hợp đồng."""
    assert set(list_kinds()) == set(_KINDS)


@pytest.mark.parametrize("kind", _KINDS)
def test_registered_with_declared_extension(kind):
    definition = get_definition(kind)
    assert definition.kind == kind
    assert definition.file_ext == _BY_KIND[kind].file_ext


@pytest.mark.parametrize("kind", _KINDS)
def test_check_permission_denies_without_export_right(kind):
    case = _BY_KIND[kind]
    user = make_user_with_permissions(f"exp-deny-{kind}", case.deny)
    with pytest.raises(PermissionDenied) as excinfo:
        get_definition(kind).check_permission(user, case.params(user))
    assert str(excinfo.value) == case.denied_message


@pytest.mark.parametrize("kind", _KINDS)
def test_check_permission_allows_with_export_right(kind):
    case = _BY_KIND[kind]
    user = make_user_with_permissions(f"exp-allow-{kind}", case.allow)
    assert get_definition(kind).check_permission(user, case.params(user)) is None


@pytest.mark.parametrize(
    ("section", "codes", "allowed"),
    [
        ("koc-orders", ("koc-orders:export",), True),
        ("orders", ("koc-orders:export",), False),
        ("koc-orders", ("orders:export",), False),
        ("orders", ("orders:export",), True),
    ],
)
def test_orders_check_permission_follows_requested_section(section, codes, allowed):
    """`orders` check `f"{section}:export"` — KHÔNG hardcode "orders:export": role chỉ
    được cấp koc-orders phải xuất được đúng trang đó và không lọt sang trang khác."""
    definition = get_definition("orders")
    user = make_user_with_permissions(f"exp-orders-{section}-{codes[0]}", codes)
    params = _orders_params(section)(user)
    if allowed:
        assert definition.check_permission(user, params) is None
    else:
        with pytest.raises(PermissionDenied) as excinfo:
            definition.check_permission(user, params)
        assert str(excinfo.value) == PAGE_DENIED


@pytest.mark.parametrize(
    ("kind", "params", "expected"),
    [(c.kind, params, expected) for c in CASES for params, expected in c.file_names],
    ids=lambda v: v.pattern if isinstance(v, re.Pattern) else str(v),
)
def test_file_name(kind, params, expected):
    name = get_definition(kind).file_name(params)
    if isinstance(expected, re.Pattern):
        assert expected.fullmatch(name), name
    else:
        assert name == expected


@pytest.mark.parametrize(
    ("kind", "params"),
    [(c.kind, params) for c in CASES for params in c.estimate_none],
)
def test_estimate_returns_none_when_counting_is_not_cheap(kind, params):
    assert get_definition(kind).estimate(params) is None
