# Huỷ phiếu trả hàng posted giờ cần returns:approve (trước chỉ cần section
# returns). Cấp returns:approve cho mọi role đang có stock-documents:approve
# để nhóm kế toán duyệt kho không bị mất quyền huỷ phiếu trả sau khi siết gate.

from django.db import migrations

_SOURCE_CODE = "stock-documents:approve"
_TARGET_CODE = "returns:approve"


def grant_returns_approve(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    RolePermission = apps.get_model("users", "RolePermission")

    target, _ = Permission.objects.get_or_create(
        code=_TARGET_CODE,
        defaults={"section": "returns", "action": "approve", "label": ""},
    )
    role_ids = RolePermission.objects.filter(
        permission__code=_SOURCE_CODE
    ).values_list("role_id", flat=True)
    for role_id in role_ids:
        RolePermission.objects.get_or_create(role_id=role_id, permission=target)


def revoke_returns_approve(apps, schema_editor):
    RolePermission = apps.get_model("users", "RolePermission")
    source_role_ids = set(
        RolePermission.objects.filter(permission__code=_SOURCE_CODE).values_list(
            "role_id", flat=True
        )
    )
    RolePermission.objects.filter(
        permission__code=_TARGET_CODE, role_id__in=source_role_ids
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0022_landing_orders_scope_all"),
    ]

    operations = [
        migrations.RunPython(grant_returns_approve, revoke_returns_approve),
    ]
