"""Migration seed: role đang sửa được đơn phải giữ nguyên quyền sau khi lên fail-closed.

Không seed = ngày deploy cả công ty mất quyền sửa đơn.

Gọi thẳng hàm `seed` của module migration với registry model thật: migration đã chạy
lúc dựng test DB nên bảng đã có dữ liệu seed sẵn; test xoá sạch rồi gọi lại để kiểm
đúng logic chọn role, không phụ thuộc trạng thái seed lúc migrate.
"""

import importlib

import pytest
from django.apps import apps as django_apps

from apps.users.models import Permission, Role, RoleFieldGrant, RolePermission
from apps.users.rbac_catalog import ALL_ORDER_FIELDS

seed_migration = importlib.import_module("apps.users.migrations.0036_seed_field_grants")


def _role_with_permission(name: str, code: str) -> Role:
    role = Role.objects.create(name=name)
    section, action = code.split(":", 1)
    permission, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action, "label": code}
    )
    RolePermission.objects.create(role=role, permission=permission)
    return role


@pytest.mark.django_db
def test_seed_grants_every_catalog_field_to_order_editing_roles():
    editor = _role_with_permission("Role sửa đơn", "orders:edit")
    # Đối chứng phải nằm ở section KHÁC HẲN: `orders:view` giờ cũng được seed (test
    # ngay dưới), lấy nó làm role "không dính dáng đơn" là test tự lừa mình.
    outsider = _role_with_permission("Role ngoài mảng đơn", "customers:view")
    RoleFieldGrant.objects.all().delete()

    seed_migration.seed(django_apps, None)

    granted = set(
        RoleFieldGrant.objects.filter(role=editor).values_list("field", flat=True)
    )
    assert granted == set(ALL_ORDER_FIELDS)
    assert not RoleFieldGrant.objects.filter(role=outsider).exists()


@pytest.mark.django_db
def test_seed_covers_view_only_role():
    """`update_order_inline` gác bằng `require_section` = `{section}:view`; KHÔNG có
    chỗ nào check `:edit`. Role chỉ có `orders:view` hôm nay vẫn sửa đơn hằng ngày —
    bỏ sót nhóm này là họ mất sạch quyền ngay giây roll code."""
    viewer = _role_with_permission("Role chỉ xem", "orders:view")
    RoleFieldGrant.objects.all().delete()

    seed_migration.seed(django_apps, None)

    assert RoleFieldGrant.objects.filter(role=viewer).count() == len(ALL_ORDER_FIELDS)


@pytest.mark.django_db
def test_seed_covers_shipments_edit_role():
    """Phòng Kho có shipments:edit và PATCH đơn qua cùng endpoint — bỏ sót là kho
    mất quyền sửa mã vận đơn ngay ngày deploy."""
    warehouse = _role_with_permission("Role kho", "shipments:edit")
    RoleFieldGrant.objects.all().delete()

    seed_migration.seed(django_apps, None)

    assert RoleFieldGrant.objects.filter(role=warehouse).count() == len(ALL_ORDER_FIELDS)


@pytest.mark.django_db
def test_seed_is_rerunnable():
    """`ignore_conflicts=True` phải chịu được chạy lại — migration replay không vỡ."""
    _role_with_permission("Role sửa đơn 2", "orders:edit")
    RoleFieldGrant.objects.all().delete()

    seed_migration.seed(django_apps, None)
    before = RoleFieldGrant.objects.count()
    seed_migration.seed(django_apps, None)

    assert RoleFieldGrant.objects.count() == before
