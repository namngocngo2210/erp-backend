from django.db import migrations

CODE = "customer-explorer:export"


def drop(apps, schema_editor):
    """Thu hồi quyền export của trang Khám phá dữ liệu.

    Trang đã chuyển thành màn Hồ sơ 360 từng khách, không còn bảng nào để xuất.
    Các dòng `RolePermission` trỏ tới quyền này (nếu có role nào từng được cấp)
    tự mất theo: FK `permission` khai báo `on_delete=CASCADE`.
    """
    Permission = apps.get_model("users", "Permission")
    Permission.objects.filter(code=CODE).delete()


def restore(apps, schema_editor):
    """Tạo lại Permission rỗng (không cấp lại cho role nào — không có gì để suy ra)."""
    Permission = apps.get_model("users", "Permission")
    section, action = CODE.split(":")
    Permission.objects.get_or_create(
        code=CODE,
        defaults={"section": section, "action": action, "label": f"{section} · {action}"},
    )


class Migration(migrations.Migration):
    dependencies = [("users", "0040_seed_customer_explorer_permissions")]
    operations = [migrations.RunPython(drop, restore)]
