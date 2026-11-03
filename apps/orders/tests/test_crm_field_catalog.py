"""Catalog quyền sửa phải phủ đúng từng trường đánh giá CSKH.

`apps.users` KHÔNG được import `apps.orders` (.importlinter) nên danh sách tên
trường buộc phải chép sang `rbac_catalog.py`. Test này là van chống trôi: thêm cột
đánh giá tĩnh mới mà quên khai vào catalog thì trường đó nằm ngoài tầm quản lý của
quyền — ai cũng sửa được, đúng thứ allow-list sinh ra để chặn.
"""

from apps.orders.assessment_fields import ASSESSMENT_FIELD_NAMES
from apps.users.rbac_catalog import (
    ALL_ORDER_FIELDS,
    CRM_EXTRA_FIELD,
    CRM_FIELD_PREFIX,
    CRM_NEEDS_FIELD,
    ORDER_FIELD_LABELS,
)


def _crm_grant_names() -> set[str]:
    return {f for f in ALL_ORDER_FIELDS if f.startswith(CRM_FIELD_PREFIX)}


def test_every_static_assessment_field_is_configurable():
    static_grants = _crm_grant_names() - {CRM_NEEDS_FIELD, CRM_EXTRA_FIELD}
    expected = {f"{CRM_FIELD_PREFIX}{name}" for name in ASSESSMENT_FIELD_NAMES}
    assert static_grants == expected


def test_needs_and_extra_have_their_own_entries():
    crm_grants = _crm_grant_names()
    assert CRM_NEEDS_FIELD in crm_grants
    assert CRM_EXTRA_FIELD in crm_grants


def test_block_level_crm_grant_no_longer_exists():
    """Hạt cũ `crm` phải biến mất — còn lại là hai cơ chế cùng gác một cửa."""
    assert "crm" not in ALL_ORDER_FIELDS


def test_every_crm_grant_has_a_vietnamese_label():
    missing = {f for f in _crm_grant_names() if not ORDER_FIELD_LABELS.get(f)}
    assert missing == set()
