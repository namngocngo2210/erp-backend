"""Dọn quyền "sync" đã chết — trang Đồng bộ (SyncPage) gỡ 2026-07-14.

Không endpoint nào từng gate `require_section("sync")` (2 endpoint của trang gate
"kiotviet" + admin), nên các dòng sync:view/sync:edit đã seed chỉ gây hiểu nhầm khi
phân quyền. Xoá Permission sẽ cascade RolePermission/UserPermission.
"""
from django.db import migrations

SYNC_CODES = ("sync:view", "sync:edit")


def drop_sync_permissions(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    Permission.objects.filter(code__in=SYNC_CODES).delete()


def noop(apps, schema_editor):
    # Không seed lại khi rollback: catalog không còn section "sync";
    # sync_permissions() sẽ tự seed nếu section được thêm lại vào catalog.
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0027_seed_salaries_edit_export")]
    operations = [migrations.RunPython(drop_sync_permissions, noop)]
