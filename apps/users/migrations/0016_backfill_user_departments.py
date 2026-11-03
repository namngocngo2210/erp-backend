from django.db import migrations


def run(apps, schema_editor):
    """Backfill UserDepartment (1 dòng is_primary) từ User.department FK. Idempotent.

    Dùng model lịch sử (apps.get_model) — bản cũ import model live nên mọi field
    thêm vào User sau migration này (vd. zenify_email ở 0045) làm migrate DB mới
    (test/CI) đổ vì SELECT cột chưa tồn tại.
    """
    User = apps.get_model("users", "User")
    UserDepartment = apps.get_model("users", "UserDepartment")
    for user in User.objects.filter(department__isnull=False).only("id", "department_id"):
        UserDepartment.objects.get_or_create(
            user=user, department_id=user.department_id, defaults={"is_primary": True}
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0015_backfill_roles_from_legacy"),
    ]

    operations = [migrations.RunPython(run, noop)]
