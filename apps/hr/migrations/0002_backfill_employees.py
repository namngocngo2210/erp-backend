from django.db import migrations

from apps.hr.domain.employee_code import next_employee_code

# Đóng băng logic backfill tại thời điểm 0002: ở trạng thái lịch sử này
# ``Employee`` VẪN còn field ``user`` (OneToOne). Migration sau mới tách quan hệ
# sang ``EmployeeAccount``, nên ở đây tạo Employee kèm ``user`` là đúng lịch sử.
# Inline để migration độc lập với code live (vốn đã đổi sang model 1-N).
_BOT_USERNAMES = {"lark"}


def run(apps, schema_editor):
    User = apps.get_model("users", "User")
    Employee = apps.get_model("hr", "Employee")

    existing_user_ids = set(Employee.objects.values_list("user_id", flat=True))
    latest = (
        Employee.objects.order_by("-employee_code")
        .values_list("employee_code", flat=True)
        .first()
    )
    for user in User.objects.filter(is_active=True, deleted_at__isnull=True).order_by("id"):
        if user.id in existing_user_ids or user.username in _BOT_USERNAMES:
            continue
        latest = next_employee_code(latest)
        full_name = (user.display_name or "").strip() or (
            f"{user.first_name} {user.last_name}".strip()
        ) or user.username
        Employee.objects.create(
            user=user,
            employee_code=latest,
            full_name=full_name,
            department_id=user.department_id,
            employment_status="active",
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("hr", "0001_initial"),
        ("users", "0018_seed_hr_permissions"),
    ]
    operations = [migrations.RunPython(run, noop)]
