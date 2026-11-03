"""Bung grant khối `crm` thành từng trường đánh giá CSKH.

Quyền sửa-theo-field trước đây coi cả khối CSKH là MỘT hạt (`crm`). Từ nay mỗi
trường đánh giá là một hạt riêng, nên mọi role đang có `crm` phải được cấp đủ các
hạt con — nếu không, cả công ty mất quyền sửa CSKH ngay giây roll code (hệ
fail-closed: vắng grant = không sửa được).

Chiều ngược lại gộp về `crm`, để rollback không bỏ role nào lại phía sau.
"""

from django.db import migrations

SECTION = "orders"
BLOCK_FIELD = "crm"


def _crm_subfields():
    from apps.users.rbac_catalog import ALL_ORDER_FIELDS, CRM_FIELD_PREFIX

    return sorted(f for f in ALL_ORDER_FIELDS if f.startswith(CRM_FIELD_PREFIX))


def expand(apps, schema_editor):
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")

    role_ids = sorted(
        RoleFieldGrant.objects.filter(section=SECTION, field=BLOCK_FIELD).values_list(
            "role_id", flat=True
        )
    )
    if not role_ids:
        return

    RoleFieldGrant.objects.bulk_create(
        [
            RoleFieldGrant(role_id=rid, section=SECTION, field=f)
            for rid in role_ids
            for f in _crm_subfields()
        ],
        ignore_conflicts=True,
    )
    RoleFieldGrant.objects.filter(section=SECTION, field=BLOCK_FIELD).delete()


def collapse(apps, schema_editor):
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")
    prefix = "crm."

    role_ids = sorted(
        RoleFieldGrant.objects.filter(section=SECTION, field__startswith=prefix)
        .values_list("role_id", flat=True)
        .distinct()
    )
    if not role_ids:
        return

    RoleFieldGrant.objects.bulk_create(
        [RoleFieldGrant(role_id=rid, section=SECTION, field=BLOCK_FIELD) for rid in role_ids],
        ignore_conflicts=True,
    )
    RoleFieldGrant.objects.filter(section=SECTION, field__startswith=prefix).delete()


def bump_cache(apps, schema_editor):
    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()


class Migration(migrations.Migration):
    dependencies = [("users", "0036_seed_field_grants")]

    operations = [
        migrations.RunPython(expand, collapse),
        migrations.RunPython(bump_cache, migrations.RunPython.noop),
    ]
