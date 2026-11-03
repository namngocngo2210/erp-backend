"""Migration bung grant `crm` thành từng trường đánh giá.

Hệ fail-closed: role đang có `crm` mà không được bung sẽ mất sạch quyền sửa CSKH
ngay giây roll code. Test xoá sạch grant trước khi gọi để kiểm ĐÚNG logic của hàm,
không phải trạng thái sẵn có do migration chạy lúc dựng test DB.
"""

import importlib

import pytest
from django.apps import apps as django_apps

from apps.users.models import Role, RoleFieldGrant
from apps.users.rbac_catalog import ALL_ORDER_FIELDS, CRM_FIELD_PREFIX

migration = importlib.import_module("apps.users.migrations.0037_expand_crm_field_grants")

CRM_SUBFIELDS = {f for f in ALL_ORDER_FIELDS if f.startswith(CRM_FIELD_PREFIX)}


def _grants(role) -> set[str]:
    return set(RoleFieldGrant.objects.filter(role=role).values_list("field", flat=True))


@pytest.mark.django_db
def test_block_grant_becomes_every_subfield():
    role = Role.objects.create(name="Role co crm")
    RoleFieldGrant.objects.all().delete()
    RoleFieldGrant.objects.create(role=role, section="orders", field="crm")

    migration.expand(django_apps, None)

    assert _grants(role) == CRM_SUBFIELDS


@pytest.mark.django_db
def test_role_without_crm_is_untouched():
    role = Role.objects.create(name="Role khong co crm")
    RoleFieldGrant.objects.all().delete()
    RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

    migration.expand(django_apps, None)

    assert _grants(role) == {"phone"}


@pytest.mark.django_db
def test_expand_is_rerunnable():
    role = Role.objects.create(name="Role rerun")
    RoleFieldGrant.objects.all().delete()
    RoleFieldGrant.objects.create(role=role, section="orders", field="crm")

    migration.expand(django_apps, None)
    before = _grants(role)
    migration.expand(django_apps, None)

    assert _grants(role) == before


@pytest.mark.django_db
def test_collapse_restores_the_block_grant():
    """Rollback không được bỏ role nào lại phía sau."""
    role = Role.objects.create(name="Role rollback")
    RoleFieldGrant.objects.all().delete()
    RoleFieldGrant.objects.bulk_create(
        RoleFieldGrant(role=role, section="orders", field=f) for f in sorted(CRM_SUBFIELDS)
    )

    migration.collapse(django_apps, None)

    assert _grants(role) == {"crm"}
